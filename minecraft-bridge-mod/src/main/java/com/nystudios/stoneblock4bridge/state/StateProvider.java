package com.nystudios.stoneblock4bridge.state;

import com.nystudios.stoneblock4bridge.dto.GameStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.InventorySnapshotDto;
import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import com.nystudios.stoneblock4bridge.dto.OpenScreenStateDto;
import com.nystudios.stoneblock4bridge.dto.PlayerStateDto;

/**
 * Provides deterministic game-state snapshots from the client.
 */
public interface StateProvider {
    PlayerStateDto getCurrentPlayerState(long tick);

    InventorySnapshotDto getInventoryContents();

    NearbyObservationDto getNearbyObservation(int radius);

    OpenScreenStateDto getOpenScreenState();

    GameStateSnapshotDto getFullStateSnapshot(long tick);
}
