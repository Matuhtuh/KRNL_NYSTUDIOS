package com.nystudios.stoneblock4bridge.dto;

/**
 * Nearby entity observation used for threat and interaction awareness.
 */
public record EntityObservationDto(
        String entityId,
        String name,
        double x,
        double y,
        double z,
        float health,
        boolean hostile
) {
}
