package com.nystudios.stoneblock4bridge.action;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.Set;

/**
 * Minimal action executor shell.
 */
public final class ClientActionExecutor implements ActionExecutor {
    private static final Set<String> SUPPORTED_ACTION_TYPES = Set.of(
            "noop",
            "move_look",
            "interact_use",
            "inventory_click",
            "mine_block",
            "place_block"
    );

    @Override
    public ActionResultDto performAction(ActionRequestDto request) {
        if (request.actionType() == null || request.requestId() == null) {
            return new ActionResultDto("unknown", false, false, false, "bad_request", "Missing request_id or action_type");
        }

        if (!SUPPORTED_ACTION_TYPES.contains(request.actionType())) {
            return new ActionResultDto(request.requestId(), false, false, false, "unsupported_action", "Unsupported action type");
        }

        return new ActionResultDto(
                request.requestId(),
                true,
                false,
                false,
                "not_implemented",
                "Action type recognized but Minecraft wiring is not implemented yet"
        );
    }
}
