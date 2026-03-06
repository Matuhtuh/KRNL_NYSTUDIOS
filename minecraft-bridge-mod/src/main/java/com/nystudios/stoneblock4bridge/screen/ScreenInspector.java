package com.nystudios.stoneblock4bridge.screen;

import com.nystudios.stoneblock4bridge.dto.OpenScreenStateDto;

/**
 * Reads details about currently opened GUI/screen without simulating arbitrary UI behavior.
 */
public interface ScreenInspector {
    OpenScreenStateDto inspectCurrentScreen();
}
