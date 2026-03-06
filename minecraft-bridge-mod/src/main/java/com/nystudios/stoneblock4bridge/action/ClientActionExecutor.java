package com.nystudios.stoneblock4bridge.action;

import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import java.util.List;
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
            return new ActionResultDto(
                    "unknown",
                    false,
                    false,
                    false,
                    "bad_request",
                    "Missing request_id or action_type",
                    List.of("request_id_required", "action_type_required"),
                    List.of()
            );
        }

        if (!SUPPORTED_ACTION_TYPES.contains(request.actionType())) {
            return new ActionResultDto(
                    request.requestId(),
                    false,
                    false,
                    false,
                    "unsupported_action",
                    "Unsupported action type",
                    List.of("supported_action_required"),
                    List.of()
            );
        }

        return new ActionResultDto(
                request.requestId(),
                true,
                false,
                false,
                "not_implemented",
                "Action type recognized but Minecraft input wiring remains partial",
                List.of("client_loaded", "player_present"),
                List.of("no_observable_state_change")
        );
    }
}
