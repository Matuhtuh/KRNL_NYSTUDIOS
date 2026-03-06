package com.nystudios.stoneblock4bridge.state;

import com.nystudios.stoneblock4bridge.dto.BridgeStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.InventoryItemDto;
import com.nystudios.stoneblock4bridge.dto.InventorySnapshotDto;
import com.nystudios.stoneblock4bridge.dto.NearbyObservationDto;
import com.nystudios.stoneblock4bridge.dto.PlayerStateDto;
import com.nystudios.stoneblock4bridge.screen.ClientScreenInspector;
import com.nystudios.stoneblock4bridge.screen.ScreenInspector;
import java.util.List;

/**
 * Client-side state provider with explicit placeholders.
 *
 * <p>Methods are intentionally conservative until backed by tested Minecraft integration code.</p>
 */
public final class ClientStateProvider implements StateProvider {
    private final ScreenInspector screenInspector = new ClientScreenInspector();

    @Override
    public PlayerStateDto getCurrentPlayerState() {
        return new PlayerStateDto(
                "unknown",
                0.0,
                0.0,
                0.0,
                0.0f,
                0.0f,
                20.0f,
                20,
                false,
                false,
                "minecraft:air",
                "minecraft:air"
        );
    }

    @Override
    public InventorySnapshotDto getInventoryContents() {
        return new InventorySnapshotDto(List.of(new InventoryItemDto(0, "minecraft:air", 0, true)));
    }

    @Override
    public NearbyObservationDto getNearbyObservation() {
        return new NearbyObservationDto(List.of(), List.of(), 6);
    }

    @Override
    public BridgeStateSnapshotDto getFullStateSnapshot() {
        return new BridgeStateSnapshotDto(
                getCurrentPlayerState(),
                getInventoryContents(),
                getNearbyObservation(),
                screenInspector.inspectCurrentScreen()
        );
    }
}
