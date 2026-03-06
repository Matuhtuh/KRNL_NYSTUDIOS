package com.nystudios.stoneblock4bridge.state;

import com.nystudios.stoneblock4bridge.dto.BridgeStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.InventorySnapshotDto;
import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import com.nystudios.stoneblock4bridge.dto.PlayerStateDto;

/**
 * Provides deterministic game-state snapshots from the client.
 */
public interface StateProvider {
    PlayerStateDto getCurrentPlayerState();

    InventorySnapshotDto getInventoryContents();

    NearbyObservationDto getNearbyObservation();

    BridgeStateSnapshotDto getFullStateSnapshot();
}
