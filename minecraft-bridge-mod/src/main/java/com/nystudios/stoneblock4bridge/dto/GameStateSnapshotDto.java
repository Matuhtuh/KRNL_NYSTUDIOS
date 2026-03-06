package com.nystudios.stoneblock4bridge.dto;

import java.util.List;

/**
 * Top-level state payload consumed by the Python brain loop.
 */
public record GameStateSnapshotDto(
        PlayerStateDto player,
        InventorySnapshotDto inventory,
        List<BlockObservationDto> nearbyBlocks,
        List<EntityObservationDto> nearbyEntities,
        OpenScreenStateDto openScreen
) {
}
