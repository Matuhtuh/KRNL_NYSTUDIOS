package com.nystudios.stoneblock4bridge.dto;

/**
 * Information about currently open GUI/screen for container and crafting workflows.
 */
public record OpenScreenStateDto(
        boolean screenOpen,
        String screenClass,
        String title,
        int slotCount
) {
}
