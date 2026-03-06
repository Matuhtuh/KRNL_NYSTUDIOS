package com.nystudios.stoneblock4bridge.dto;

/**
 * Combined state payload returned to the local external AI service.
 */
public record BridgeStateSnapshotDto(
        PlayerStateDto player,
        InventorySnapshotDto inventory,
        NearbyObservationDto nearby,
        OpenScreenStateDto screen
) {
}
