package com.nystudios.stoneblock4bridge.observe;

import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;

/**
 * Captures nearby block/entity observations around the player.
 */
public interface WorldObserver {
    NearbyObservationDto sampleNearby();
}
