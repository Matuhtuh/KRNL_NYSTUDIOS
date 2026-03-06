package com.nystudios.stoneblock4bridge.screen;

import com.nystudios.stoneblock4bridge.dto.OpenScreenStateDto;

/**
 * Placeholder screen inspector for future container/crafting UI parsing.
 */
public final class ClientScreenInspector implements ScreenInspector {
    @Override
    public OpenScreenStateDto inspectCurrentScreen() {
        return new OpenScreenStateDto(false, "none", "No screen", 0);
    }
}
