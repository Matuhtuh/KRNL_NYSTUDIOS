package com.nystudios.stoneblock4bridge.dto;

/**
 * One inventory slot snapshot.
 */
public record InventoryItemDto(
        int slot,
        String itemId,
        int count,
        boolean empty
) {
}
