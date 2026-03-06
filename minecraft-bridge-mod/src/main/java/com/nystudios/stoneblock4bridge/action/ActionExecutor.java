package com.nystudios.stoneblock4bridge.action;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;

/**
 * Executes bounded player actions. No hidden autonomy or unsafe automation should be added here.
 */
public interface ActionExecutor {
    ActionResultDto performAction(ActionRequestDto request);
}
