package io.suret.sb4bridge;

import com.mojang.logging.LogUtils;
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;
import net.neoforged.fml.loading.FMLPaths;
import net.neoforged.neoforge.client.event.ClientTickEvent;
import net.neoforged.neoforge.common.NeoForge;
import org.slf4j.Logger;

import java.nio.file.Path;

@Mod(value = SB4BaritoneBridgeMod.MODID, dist = Dist.CLIENT)
public final class SB4BaritoneBridgeMod {
    public static final String MODID = "sb4baritonebridge";
    public static final Logger LOGGER = LogUtils.getLogger();

    private final BridgeRuntime bridgeRuntime;

    public SB4BaritoneBridgeMod() {
        Path bridgeDir = FMLPaths.CONFIGDIR.get().resolve("sb4_baritone_bridge");
        this.bridgeRuntime = new BridgeRuntime(bridgeDir);
        NeoForge.EVENT_BUS.addListener(this::onClientTickPost);
        LOGGER.info("[{}] initialized bridge directory at {}", MODID, bridgeDir);
    }

    private void onClientTickPost(final ClientTickEvent.Post event) {
        bridgeRuntime.tick();
    }
}
