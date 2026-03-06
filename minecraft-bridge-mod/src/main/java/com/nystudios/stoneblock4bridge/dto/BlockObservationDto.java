package com.nystudios.stoneblock4bridge.dto;

/**
 * Nearby block observation for local navigation/mining/build checks.
 */
public record BlockObservationDto(
        String blockId,
        int x,
        int y,
        int z,
        boolean replaceable,
        float hardness
) {
}
