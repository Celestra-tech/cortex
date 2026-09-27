from fastapi import APIRouter

from cortex_api.api.deps import OrganizationDep, RouterDep
from cortex_api.schemas.completion import ModelListResponse, ModelPricing, ModelRead
from cortex_api.services.router.policies import OrganizationPolicy

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelListResponse)
async def list_models(organization: OrganizationDep, cortex_router: RouterDep) -> ModelListResponse:
    policy = OrganizationPolicy.from_organization_settings(organization.settings)
    registry = cortex_router.registry
    models = []
    for spec in registry.models():
        availability = registry.availability(spec)
        models.append(
            ModelRead(
                id=spec.key,
                provider=spec.provider,
                model=spec.id,
                display_name=spec.display_name,
                capabilities=sorted(spec.capabilities),
                context_window=spec.context_window,
                max_output_tokens=spec.max_output_tokens,
                pricing=ModelPricing(
                    input_per_mtok=float(spec.input_cost_per_mtok),
                    output_per_mtok=float(spec.output_cost_per_mtok),
                ),
                quality=spec.quality,
                expected_latency_ms=round(registry.expected_latency_ms(spec)),
                available=availability.available,
                availability=str(availability.reason),
                allowed=policy.rejection(spec) is None,
            )
        )
    return ModelListResponse(models=models)
