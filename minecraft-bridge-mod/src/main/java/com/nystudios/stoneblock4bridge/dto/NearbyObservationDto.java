package com.nystudios.stoneblock4bridge.dto;

import java.util.List;

/**
 * Combined local environment snapshot around the player.
 */
public record NearbyObservationDto(
        List<BlockObservationDto> blocks,
        List<EntityObservationDto> entities,
        int sampleRadius
) {
}
