package com.nystudios.stoneblock4bridge.dto;

import java.util.List;

/**
 * Inventory snapshot returned to the external AI brain.
 */
public record InventorySnapshotDto(
        List<InventoryItemDto> items,
        List<InventoryItemDto> hotbar,
        boolean partial,
        String note
) {
}
