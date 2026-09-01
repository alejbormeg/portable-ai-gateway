# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from portable_ai_gateway.provider_credentials import PROVIDER_CREDENTIAL_ENV_PATTERN
from portable_ai_gateway.provider_endpoints import (
    VERTEX_LOCATION_PATTERN,
    VERTEX_PROJECT_PATTERN,
    validate_provider_base_url,
)


IDENTIFIER_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$"
AUDIT_TEXT_PATTERN = r"^[^\x00-\x1f\x7f]+$"
CONFIG_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$"
API_KEY_SCOPES = frozenset({"models:read", "inference:write"})
UPSTREAM_MODEL_PATTERN = r"^[^\x00-\x1f\x7f]+$"
DEFAULT_PROVIDER_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MIN_PROVIDER_MAX_RESPONSE_BYTES = 1024
MAX_PROVIDER_MAX_RESPONSE_BYTES = 64 * 1024 * 1024


class Resource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["pag/v1"] = "pag/v1"
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    tenant_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    enabled: bool = True
    labels: dict[str, str] = Field(default_factory=dict)


class Tenant(Resource):
    tenant_id: None = None
    name: str = Field(min_length=1, max_length=200)
    sensitive: bool = False


class Provider(Resource):
    kind: Literal["mock", "openai-compatible", "vertex"]
    base_url: str | None = None
    credential_env: str | None = Field(default=None, pattern=PROVIDER_CREDENTIAL_ENV_PATTERN)
    project: str | None = Field(default=None, pattern=VERTEX_PROJECT_PATTERN)
    location: str | None = Field(default=None, pattern=VERTEX_LOCATION_PATTERN)
    residency: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    native_responses: bool = False
    max_response_bytes: int = Field(
        default=DEFAULT_PROVIDER_MAX_RESPONSE_BYTES,
        ge=MIN_PROVIDER_MAX_RESPONSE_BYTES,
        le=MAX_PROVIDER_MAX_RESPONSE_BYTES,
    )

    @model_validator(mode="after")
    def validate_provider(self) -> "Provider":
        if self.kind == "openai-compatible" and not self.base_url:
            raise ValueError("base_url is required for openai-compatible providers")
        if self.base_url:
            validate_provider_base_url(self.base_url)
        if self.kind == "vertex" and not (self.project and self.location):
            raise ValueError("project and location are required for Vertex providers")
        return self


class Capabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chat: bool = True
    responses: bool = False
    streaming: bool = True
    tools: bool = False
    structured_output: bool = False
    multimodal: bool = False


class Model(Resource):
    upstream_model: str = Field(min_length=1, max_length=300, pattern=UPSTREAM_MODEL_PATTERN)
    capabilities: Capabilities = Field(default_factory=Capabilities)


class RouteTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: str = Field(pattern=IDENTIFIER_PATTERN)
    upstream_model: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        pattern=UPSTREAM_MODEL_PATTERN,
    )
    residency: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    priority: int = Field(default=100, ge=0)


class Route(Resource):
    model_id: str = Field(pattern=IDENTIFIER_PATTERN)
    targets: list[RouteTarget] = Field(min_length=1, max_length=32)
    required_residency: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, targets: list[RouteTarget]) -> list[RouteTarget]:
        identifiers = [target.provider_id for target in targets]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("route target provider IDs must be unique")
        return sorted(targets, key=lambda item: item.priority)


class Policy(Resource):
    max_input_chars: int | None = Field(default=None, ge=1)
    denied_parameters: list[str] = Field(default_factory=list)


class Quota(Resource):
    requests_per_minute: int | None = Field(default=None, ge=1)
    concurrent_requests: int | None = Field(default=None, ge=1)


RESOURCE_MODELS: dict[str, type[Resource]] = {
    "tenants": Tenant,
    "providers": Provider,
    "models": Model,
    "routes": Route,
    "policies": Policy,
    "quotas": Quota,
}


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    tenant_id: str = Field(pattern=IDENTIFIER_PATTERN)
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, scopes: list[str]) -> list[str]:
        normalized = sorted(set(scopes))
        unsupported = sorted(set(normalized) - API_KEY_SCOPES)
        if unsupported:
            raise ValueError(f"API keys cannot receive scopes: {', '.join(unsupported)}")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        if value is not None and value <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return value


class ApiKeyView(BaseModel):
    id: str
    tenant_id: str
    name: str
    scopes: list[str]
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None


class ApiKeyIssued(ApiKeyView):
    key: str


class ConfigurationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=CONFIG_VERSION_PATTERN)


class ConfigurationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenants: list[Tenant] = Field(default_factory=list)
    providers: list[Provider] = Field(default_factory=list)
    models: list[Model] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    quotas: list[Quota] = Field(default_factory=list)


class ConfigurationDocument(BaseModel):
    """Versioned, portable GitOps configuration contract."""

    model_config = ConfigDict(extra="forbid")
    api_version: Literal["gateway.portable-ai.dev/v1alpha1"] = "gateway.portable-ai.dev/v1alpha1"
    kind: Literal["GatewayConfiguration"] = "GatewayConfiguration"
    metadata: ConfigurationMetadata
    spec: ConfigurationSpec


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    model: str = Field(pattern=IDENTIFIER_PATTERN)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    model: str = Field(pattern=IDENTIFIER_PATTERN)
    input: Any
    stream: bool = False

    @field_validator("input")
    @classmethod
    def non_null_input(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("input cannot be null")
        return value


class AuditEvent(BaseModel):
    """Metadata-only audit event. Request and response content are intentionally absent."""

    FORBIDDEN_CONTENT_FIELDS: ClassVar[set[str]] = {"input", "messages", "prompt", "response", "body", "content"}
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=IDENTIFIER_PATTERN)
    timestamp: datetime
    request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
    actor: str = Field(min_length=1, max_length=256, pattern=AUDIT_TEXT_PATTERN)
    tenant_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    action: str = Field(pattern=IDENTIFIER_PATTERN)
    resource: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    model: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    route: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    decision: str | None = Field(default=None, min_length=1, max_length=8192, pattern=AUDIT_TEXT_PATTERN)
    provider: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    status: int = Field(ge=100, le=599)
    config_version: str = Field(pattern=CONFIG_VERSION_PATTERN)
    error_code: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
