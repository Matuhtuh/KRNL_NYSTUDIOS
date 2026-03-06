package com.nystudios.stoneblock4bridge.observe;

import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import java.util.List;

/**
 * Minimal world observer placeholder.
 */
public final class ClientWorldObserver implements WorldObserver {
    @Override
    public NearbyObservationDto sampleNearby() {
        return new NearbyObservationDto(List.of(), List.of(), 6);
    }
}
