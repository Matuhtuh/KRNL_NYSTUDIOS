package com.nystudios.stoneblock4bridge;

import com.nystudios.stoneblock4bridge.action.ActionExecutor;
import com.nystudios.stoneblock4bridge.action.ClientActionExecutor;
import com.nystudios.stoneblock4bridge.bridge.BridgeServer;
import com.nystudios.stoneblock4bridge.bridge.LocalHttpBridgeServer;
import com.nystudios.stoneblock4bridge.state.ClientStateProvider;
import com.nystudios.stoneblock4bridge.state.StateProvider;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.event.lifecycle.FMLClientStoppingEvent;
import net.neoforged.fml.loading.FMLEnvironment;

/**
 * NeoForge entrypoint for a client-only bridge mod.
 */
@Mod(value = StoneBlock4BridgeMod.MOD_ID, dist = Dist.CLIENT)
public final class StoneBlock4BridgeMod {
    public static final String MOD_ID = "stoneblock4bridge";

    private final BridgeServer bridgeServer;

    public StoneBlock4BridgeMod(IEventBus eventBus) {
        if (FMLEnvironment.dist != Dist.CLIENT) {
            throw new IllegalStateException("stoneblock4bridge must run on client only");
        }

        StateProvider stateProvider = new ClientStateProvider();
        ActionExecutor actionExecutor = new ClientActionExecutor();
        this.bridgeServer = new LocalHttpBridgeServer(stateProvider, actionExecutor);
        this.bridgeServer.start();

        eventBus.addListener(this::onClientStopping);
    }

    private void onClientStopping(FMLClientStoppingEvent event) {
        bridgeServer.stop();
    }
}
