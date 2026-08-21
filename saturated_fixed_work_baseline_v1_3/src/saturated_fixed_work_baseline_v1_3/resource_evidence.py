"""v1.3 current-campaign resource gate re-export."""

from saturated_fixed_work_baseline_v1_2.v1_3 import (
    CampaignResourceError,
    ResourceIdentitySnapshot,
    RESOURCE_ENVELOPE_SCHEMA,
    build_campaign_resource_envelope,
    capture_resource_identity,
    compare_campaign_resource_envelopes,
    require_campaign_resource_gate,
    require_same_campaign_resource_envelope,
    requalify_after_restart,
)

__all__ = [
    "CampaignResourceError",
    "ResourceIdentitySnapshot",
    "RESOURCE_ENVELOPE_SCHEMA",
    "build_campaign_resource_envelope",
    "capture_resource_identity",
    "compare_campaign_resource_envelopes",
    "require_campaign_resource_gate",
    "require_same_campaign_resource_envelope",
    "requalify_after_restart",
]
