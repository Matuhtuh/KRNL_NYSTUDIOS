package com.nystudios.stoneblock4bridge.dto;

import java.util.List;

/**
 * Full inventory snapshot returned to the external AI brain.
 */
public record InventorySnapshotDto(
        List<InventoryItemDto> items
) {
}
