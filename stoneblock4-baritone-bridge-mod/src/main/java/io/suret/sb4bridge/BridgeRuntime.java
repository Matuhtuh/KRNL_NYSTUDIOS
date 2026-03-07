package io.suret.sb4bridge;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.food.FoodData;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.monster.Monster;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.CraftingMenu;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.crafting.CraftingRecipe;
import net.minecraft.world.item.crafting.Ingredient;
import net.minecraft.world.item.crafting.RecipeHolder;
import net.minecraft.world.item.crafting.RecipeType;
import net.minecraft.world.item.crafting.ShapedRecipe;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.UseAnim;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Pattern;

final class BridgeRuntime {
    private static final String BRIDGE_VERSION = "0.1.8";
    private static final int STATUS_SCHEMA_VERSION = 2;
    private static final String MODPACK_NAME = "FTB StoneBlock 4";
    private static final int MAX_PENDING_RESPONSES = 512;
    private static final int MAX_RECENT_RESPONSES = 2048;
    private static final int MAX_PENDING_FLUSH_PER_TICK = 8;
    private static final int MAX_FAILURE_REASON_BUCKETS = 96;
    private static final int MAX_FAILURE_REASON_LENGTH = 96;
    private static final int MAX_FAILURE_TOKEN_LENGTH = 64;
    private static final long COMMAND_FAILURE_WARNING_INTERVAL_MS = 30_000L;
    private static final Pattern SAFE_ID_PATTERN = Pattern.compile("[A-Za-z0-9._-]+");
    private static final Set<String> STONEBLOCK_KEY_ITEM_IDS = Set.of(
            "minecraft:cobblestone",
            "minecraft:cobbled_deepslate",
            "minecraft:gravel",
            "minecraft:sand",
            "minecraft:dirt",
            "minecraft:clay_ball",
            "minecraft:crafting_table",
            "minecraft:furnace",
            "ftbstuff:stone_hammer",
            "ftbstuff:iron_hammer",
            "ftbstuff:diamond_hammer",
            "ftbstuff:iron_brush"
    );
    private static final long STATUS_WRITE_INTERVAL_MS = 350L;
    private static final int MAX_CRAFT_COMMAND_COUNT = 256;
    private static final int CRAFT_RESULT_SETTLE_ATTEMPTS = 5;
    private static final long CRAFT_RESULT_SETTLE_SLEEP_MS = 50L;
    private static final Set<String> ULTIMINE_KEY_TRANSLATION_KEYS = Set.of(
            "key.ftbultimine",
            "ftbultimine",
            "key_key.ftbultimine"
    );
    private static final List<String> BRIDGE_CAPABILITIES = List.of(
            "bridge.swap_to_hotbar",
            "bridge.craft_item",
            "bridge.drop_item",
            "bridge.fight_hostile",
            "bridge.store_to_nearby_chest",
            "bridge.ultimine"
    );

    private final Path bridgeDir;
    private final Path inboxDir;
    private final Path outboxDir;
    private final Path statusPath;
    private final Gson gson = new GsonBuilder().setPrettyPrinting().create();
    private final BaritoneFacade baritoneFacade = new BaritoneFacade();
    private final LinkedHashMap<String, BridgeResponse> pendingResponses = new LinkedHashMap<>();
    private final LinkedHashMap<String, BridgeResponse> recentResponses = new LinkedHashMap<>();
    private final LinkedHashMap<String, CommandFailureReasonCounter> commandFailureReasonCounters = new LinkedHashMap<>();
    private final long bridgeWriterPid = ProcessHandle.current().pid();
    private final String bridgeWriterSessionId = UUID.randomUUID().toString();

    private long lastStatusWriteMs;
    private long statusWriteSequence;
    private String lastCommandId = "";
    private String lastCommand = "";
    private String lastCommandResult = "";
    private String lastCommandError = "";
    private long lastCommandAtMs;
    private long commandFailureTotal;
    private long commandFailureWarningsEmittedTotal;
    private long commandFailureWarningsSuppressedTotal;

    BridgeRuntime(Path bridgeDir) {
        this.bridgeDir = bridgeDir;
        this.inboxDir = bridgeDir.resolve("inbox");
        this.outboxDir = bridgeDir.resolve("outbox");
        this.statusPath = bridgeDir.resolve("status.json");
    }

    void tick() {
        ensureDirectories();
        flushPendingResponses();
        processOneCommand();
        maybeWriteStatus();
    }

    private void ensureDirectories() {
        try {
            Files.createDirectories(bridgeDir);
            Files.createDirectories(inboxDir);
            Files.createDirectories(outboxDir);
        } catch (IOException ex) {
            SB4BaritoneBridgeMod.LOGGER.error("[{}] failed to create bridge directories", SB4BaritoneBridgeMod.MODID, ex);
        }
    }

    private void processOneCommand() {
        List<Path> files = listInboxFiles();
        if (files.isEmpty()) {
            return;
        }

        Path next = files.getFirst();
        BridgeCommand command;
        try {
            command = readCommand(next);
        } catch (Exception ex) {
            writeMalformedResponse(next, ex);
            deleteQuietly(next);
            return;
        }

        String commandId = normalizeId(command.id);
        command.id = commandId;

        BridgeResponse prior = recentResponses.get(commandId);
        if (prior != null) {
            BridgeResponse replay = copyResponse(prior);
            replay.processedAtMs = System.currentTimeMillis();
            writeOrQueueResponse(replay);
            deleteQuietly(next);
            return;
        }

        BaritoneFacade.ExecutionResult result = executeBridgeAware(command.command);
        if (!result.accepted) {
            recordCommandFailure(command.id, command.command, result.status, result.errorMessage);
        }
        boolean trackActive = Boolean.TRUE.equals(command.trackActive);
        if (trackActive) {
            this.lastCommandId = commandId;
            this.lastCommand = command.command;
            this.lastCommandAtMs = System.currentTimeMillis();
            this.lastCommandResult = result.status;
            this.lastCommandError = result.errorMessage;
        }

        BridgeResponse response = new BridgeResponse();
        response.id = command.id;
        response.command = command.command;
        response.status = result.status;
        response.accepted = result.accepted;
        response.error = result.errorMessage;
        response.createdAtMs = command.createdAtMs;
        response.processedAtMs = System.currentTimeMillis();
        response.trackActive = trackActive;
        response.bridgeVersion = BRIDGE_VERSION;
        writeOrQueueResponse(response);
        rememberRecentResponse(response);
        deleteQuietly(next);
    }

    private void flushPendingResponses() {
        if (pendingResponses.isEmpty()) {
            return;
        }
        List<String> delivered = new ArrayList<>();
        int processed = 0;
        for (Map.Entry<String, BridgeResponse> entry : new ArrayList<>(pendingResponses.entrySet())) {
            if (processed >= MAX_PENDING_FLUSH_PER_TICK) {
                break;
            }
            String responseId = normalizeId(entry.getKey());
            BridgeResponse response = entry.getValue();
            Path responseFile = outboxDir.resolve(responseFileKey(responseId) + ".json");
            if (writeJson(responseFile, response)) {
                delivered.add(responseId);
            }
            processed++;
        }
        for (String id : delivered) {
            pendingResponses.remove(id);
        }
    }

    private static String normalizeId(String raw) {
        String token = raw == null ? "" : raw.trim();
        if (token.isBlank()) {
            return "unknown";
        }
        return token;
    }

    private static String sanitizeId(String raw) {
        String token = normalizeId(raw);
        if (SAFE_ID_PATTERN.matcher(token).matches()) {
            return token;
        }
        StringBuilder out = new StringBuilder(token.length());
        for (int i = 0; i < token.length(); i++) {
            char ch = token.charAt(i);
            boolean ok = (ch >= 'a' && ch <= 'z')
                    || (ch >= 'A' && ch <= 'Z')
                    || (ch >= '0' && ch <= '9')
                    || ch == '.'
                    || ch == '_'
                    || ch == '-';
            out.append(ok ? ch : '_');
        }
        String sanitized = out.toString();
        if (sanitized.isBlank()) {
            return "unknown";
        }
        if (sanitized.length() > 96) {
            return sanitized.substring(0, 96);
        }
        return sanitized;
    }

    private static String shortIdHash(String raw) {
        String token = normalizeId(raw);
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(token.getBytes(StandardCharsets.UTF_8));
            StringBuilder out = new StringBuilder(16);
            for (int i = 0; i < 8 && i < bytes.length; i++) {
                int value = bytes[i] & 0xFF;
                out.append(Character.forDigit((value >>> 4) & 0xF, 16));
                out.append(Character.forDigit(value & 0xF, 16));
            }
            return out.toString();
        } catch (NoSuchAlgorithmException ignored) {
            return Integer.toHexString(token.hashCode());
        }
    }

    private static String responseFileKey(String rawId) {
        String normalized = normalizeId(rawId);
        String prefix = sanitizeId(normalized).toLowerCase(Locale.ROOT);
        if (prefix.length() > 64) {
            prefix = prefix.substring(0, 64);
        }
        return prefix + "_" + shortIdHash(normalized);
    }

    private void writeOrQueueResponse(BridgeResponse response) {
        String id = normalizeId(response == null ? null : response.id);
        if (response != null) {
            response.id = id;
        }
        Path responseFile = outboxDir.resolve(responseFileKey(id) + ".json");
        if (writeJson(responseFile, response)) {
            pendingResponses.remove(id);
            return;
        }
        if (!pendingResponses.containsKey(id) && pendingResponses.size() >= MAX_PENDING_RESPONSES) {
            String oldest = pendingResponses.keySet().iterator().next();
            pendingResponses.remove(oldest);
        }
        pendingResponses.put(id, copyResponse(response));
        SB4BaritoneBridgeMod.LOGGER.error(
                "[{}] response write failed; queued retry for command id={} file={} (pending={})",
                SB4BaritoneBridgeMod.MODID,
                id,
                responseFile,
                pendingResponses.size()
        );
    }

    private void rememberRecentResponse(BridgeResponse response) {
        String id = normalizeId(response == null ? null : response.id);
        if (response != null) {
            response.id = id;
        }
        recentResponses.put(id, copyResponse(response));
        while (recentResponses.size() > MAX_RECENT_RESPONSES) {
            String oldest = recentResponses.keySet().iterator().next();
            recentResponses.remove(oldest);
        }
    }

    private static BridgeResponse copyResponse(BridgeResponse src) {
        BridgeResponse out = new BridgeResponse();
        out.id = src.id;
        out.command = src.command;
        out.status = src.status;
        out.accepted = src.accepted;
        out.error = src.error;
        out.createdAtMs = src.createdAtMs;
        out.processedAtMs = src.processedAtMs;
        out.trackActive = src.trackActive;
        out.bridgeVersion = src.bridgeVersion;
        return out;
    }

    private void recordCommandFailure(
            String commandId,
            String commandLine,
            String status,
            String errorMessage
    ) {
        String reason = failureReasonKey(status, errorMessage);
        long now = System.currentTimeMillis();
        CommandFailureReasonCounter counter = commandFailureReasonCounters.get(reason);
        if (counter == null) {
            if (commandFailureReasonCounters.size() >= MAX_FAILURE_REASON_BUCKETS) {
                String oldest = commandFailureReasonCounters.keySet().iterator().next();
                commandFailureReasonCounters.remove(oldest);
            }
            counter = new CommandFailureReasonCounter();
            commandFailureReasonCounters.put(reason, counter);
        }

        commandFailureTotal++;
        counter.failureCount++;
        counter.lastFailureAtMs = now;
        counter.lastStatus = nonNullText(status);
        counter.lastError = nonNullText(errorMessage);

        if (counter.lastWarnAtMs == 0L || now - counter.lastWarnAtMs >= COMMAND_FAILURE_WARNING_INTERVAL_MS) {
            long suppressedSinceLastWarn = counter.suppressedSinceLastWarn;
            counter.warningCount++;
            counter.lastWarnAtMs = now;
            counter.suppressedSinceLastWarn = 0L;
            commandFailureWarningsEmittedTotal++;
            SB4BaritoneBridgeMod.LOGGER.warn(
                    "[{}] command failure reason={} status={} commandId={} command=\"{}\" error={} (failures={}, suppressedSinceLastWarn={})",
                    SB4BaritoneBridgeMod.MODID,
                    reason,
                    counter.lastStatus,
                    normalizeId(commandId),
                    summarizeForLog(commandLine, 128),
                    counter.lastError,
                    counter.failureCount,
                    suppressedSinceLastWarn
            );
            return;
        }

        counter.suppressedSinceLastWarn++;
        counter.suppressedWarningCount++;
        commandFailureWarningsSuppressedTotal++;
    }

    private CommandFailureWarningMetrics snapshotCommandFailureWarningMetrics() {
        CommandFailureWarningMetrics metrics = new CommandFailureWarningMetrics();
        metrics.warnIntervalMs = COMMAND_FAILURE_WARNING_INTERVAL_MS;
        metrics.totalFailures = commandFailureTotal;
        metrics.totalWarningsEmitted = commandFailureWarningsEmittedTotal;
        metrics.totalWarningsSuppressed = commandFailureWarningsSuppressedTotal;
        metrics.byReason = new LinkedHashMap<>();
        for (Map.Entry<String, CommandFailureReasonCounter> entry : commandFailureReasonCounters.entrySet()) {
            CommandFailureReasonCounter src = entry.getValue();
            CommandFailureReasonMetrics dest = new CommandFailureReasonMetrics();
            dest.failureCount = src.failureCount;
            dest.warningCount = src.warningCount;
            dest.suppressedWarningCount = src.suppressedWarningCount;
            dest.suppressedSinceLastWarning = src.suppressedSinceLastWarn;
            dest.lastFailureAtMs = src.lastFailureAtMs;
            dest.lastWarningAtMs = src.lastWarnAtMs;
            dest.lastStatus = src.lastStatus;
            dest.lastError = src.lastError;
            metrics.byReason.put(entry.getKey(), dest);
        }
        return metrics;
    }

    private static String failureReasonKey(String status, String errorMessage) {
        String statusToken = normalizeMetricToken(status, "unknown_status");
        String error = nonNullText(errorMessage).toLowerCase(Locale.ROOT);
        if (error.isBlank()) {
            return statusToken;
        }
        int colon = error.indexOf(':');
        if (colon > 0) {
            error = error.substring(0, colon);
        }
        if (error.startsWith("usage")) {
            error = "usage";
        }
        String detailToken = normalizeMetricToken(error, "unspecified");
        if (statusToken.equals(detailToken)) {
            return statusToken;
        }
        String combined = statusToken + "_" + detailToken;
        if (combined.length() > MAX_FAILURE_REASON_LENGTH) {
            return combined.substring(0, MAX_FAILURE_REASON_LENGTH);
        }
        return combined;
    }

    private static String normalizeMetricToken(String raw, String fallback) {
        String source = nonNullText(raw).toLowerCase(Locale.ROOT);
        StringBuilder out = new StringBuilder(source.length());
        boolean lastUnderscore = false;
        for (int i = 0; i < source.length(); i++) {
            char ch = source.charAt(i);
            boolean alphaNum = (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9');
            if (alphaNum) {
                out.append(ch);
                lastUnderscore = false;
                continue;
            }
            if (!lastUnderscore && out.length() > 0) {
                out.append('_');
                lastUnderscore = true;
            }
        }
        while (out.length() > 0 && out.charAt(out.length() - 1) == '_') {
            out.setLength(out.length() - 1);
        }
        if (out.length() == 0) {
            return fallback;
        }
        if (out.length() > MAX_FAILURE_TOKEN_LENGTH) {
            out.setLength(MAX_FAILURE_TOKEN_LENGTH);
        }
        return out.toString();
    }

    private static String nonNullText(String raw) {
        return raw == null ? "" : raw.trim();
    }

    private static String summarizeForLog(String raw, int maxLen) {
        String text = nonNullText(raw);
        if (maxLen <= 0 || text.length() <= maxLen) {
            return text;
        }
        if (maxLen <= 3) {
            return text.substring(0, maxLen);
        }
        return text.substring(0, maxLen - 3) + "...";
    }

    private BaritoneFacade.ExecutionResult executeBridgeAware(String rawCommand) {
        String command = rawCommand == null ? "" : rawCommand.trim();
        if (command.isEmpty()) {
            return new BaritoneFacade.ExecutionResult("error", false, "missing command");
        }
        String[] parts = command.split("\\s+");
        String op = parts[0].trim().toLowerCase(Locale.ROOT);
        return switch (op) {
            case "bridge.drop_item" -> handleDropItem(command);
            case "bridge.swap_to_hotbar" -> handleSwapToHotbar(command);
            case "bridge.store_to_nearby_chest" -> handleStoreToNearbyChest(command);
            case "bridge.craft_item" -> handleCraftItem(command);
            case "bridge.fight_hostile" -> handleFightHostile(command);
            case "bridge.ultimine" -> handleUltimine(command);
            default -> {
                if (op.startsWith("bridge.")) {
                    yield new BaritoneFacade.ExecutionResult("rejected", false, "unknown bridge command: " + op);
                }
                yield baritoneFacade.execute(command);
            }
        };
    }

    private BaritoneFacade.ExecutionResult handleSwapToHotbar(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length != 3) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.swap_to_hotbar <source_slot_1_36> <target_hotbar_slot_1_9>"
            );
        }
        final int sourceSlot;
        final int targetHotbarSlot;
        try {
            sourceSlot = Integer.parseInt(parts[1]);
            targetHotbarSlot = Integer.parseInt(parts[2]);
        } catch (NumberFormatException ex) {
            return new BaritoneFacade.ExecutionResult("error", false, "invalid slot values");
        }

        if (sourceSlot < 1 || sourceSlot > 36) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "source slot must be 1..36");
        }
        if (targetHotbarSlot < 1 || targetHotbarSlot > 9) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "target hotbar slot must be 1..9");
        }
        if (sourceSlot == targetHotbarSlot) {
            return new BaritoneFacade.ExecutionResult("accepted", true, "");
        }

        Minecraft mc = Minecraft.getInstance();
        LocalPlayer self = mc.player;
        if (mc.gameMode == null || self == null) {
            return new BaritoneFacade.ExecutionResult("error", false, "player or game mode unavailable");
        }

        int sourceContainerSlot = playerInventorySlotToContainerSlot(sourceSlot);
        if (sourceContainerSlot < 0) {
            return new BaritoneFacade.ExecutionResult("error", false, "invalid source slot mapping");
        }

        try {
            self.closeContainer();
            ItemStack sourceBefore = self.getInventory().getItem(sourceSlot - 1).copy();
            ItemStack targetBefore = self.getInventory().getItem(targetHotbarSlot - 1).copy();
            if (sourceBefore.isEmpty()) {
                return new BaritoneFacade.ExecutionResult("rejected", false, "source slot is empty");
            }
            mc.gameMode.handleInventoryMouseClick(
                    self.inventoryMenu.containerId,
                    sourceContainerSlot,
                    targetHotbarSlot - 1,
                    ClickType.SWAP,
                    self
            );
            ItemStack sourceAfter = self.getInventory().getItem(sourceSlot - 1);
            ItemStack targetAfter = self.getInventory().getItem(targetHotbarSlot - 1);
            boolean changed = sourceBefore.getCount() != sourceAfter.getCount()
                    || targetBefore.getCount() != targetAfter.getCount()
                    || !itemId(sourceBefore).equals(itemId(sourceAfter))
                    || !itemId(targetBefore).equals(itemId(targetAfter));
            if (!changed) {
                return new BaritoneFacade.ExecutionResult("rejected", false, "swap had no observable inventory change");
            }
            return new BaritoneFacade.ExecutionResult("accepted", true, "");
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "swap failed: " + msg);
        }
    }

    private static int playerInventorySlotToContainerSlot(int slotOneBased) {
        if (slotOneBased >= 1 && slotOneBased <= 9) {
            return 36 + (slotOneBased - 1);
        }
        if (slotOneBased >= 10 && slotOneBased <= 36) {
            return 9 + (slotOneBased - 10);
        }
        return -1;
    }

    private BaritoneFacade.ExecutionResult handleUltimine(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length != 2) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.ultimine <on|off|toggle>"
            );
        }

        String mode = parts[1].trim().toLowerCase(Locale.ROOT);
        return switch (mode) {
            case "on", "enable", "enabled", "1", "true" -> setUltimineKeyDown(true);
            case "off", "disable", "disabled", "0", "false" -> setUltimineKeyDown(false);
            case "toggle" -> setUltimineKeyDown(null);
            default -> new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.ultimine <on|off|toggle>"
            );
        };
    }

    private BaritoneFacade.ExecutionResult setUltimineKeyDown(Boolean desiredDown) {
        Minecraft mc = Minecraft.getInstance();
        KeyMapping ultimineKey = findUltimineKeyMapping(mc);
        if (ultimineKey == null) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "ftbultimine keybind not found");
        }

        boolean targetDown = desiredDown == null ? !ultimineKey.isDown() : desiredDown;
        try {
            ultimineKey.setDown(targetDown);
            return new BaritoneFacade.ExecutionResult("accepted", true, "");
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "ultimine key update failed: " + msg);
        }
    }

    private KeyMapping findUltimineKeyMapping(Minecraft mc) {
        if (mc == null || mc.options == null || mc.options.keyMappings == null) {
            return null;
        }
        for (KeyMapping key : mc.options.keyMappings) {
            if (key == null) {
                continue;
            }
            String name = nonNullText(key.getName()).toLowerCase(Locale.ROOT);
            if (ULTIMINE_KEY_TRANSLATION_KEYS.contains(name)) {
                return key;
            }
        }
        return null;
    }

    private BaritoneFacade.ExecutionResult handleDropItem(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length < 2 || parts.length > 3) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.drop_item <slot_1_36> [count_1_64|all]"
            );
        }

        final int sourceSlot;
        try {
            sourceSlot = Integer.parseInt(parts[1]);
        } catch (NumberFormatException ex) {
            return new BaritoneFacade.ExecutionResult("error", false, "invalid slot value");
        }
        if (sourceSlot < 1 || sourceSlot > 36) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "slot must be 1..36");
        }

        boolean dropAll = true;
        int dropCount = 1;
        if (parts.length == 3) {
            String arg = parts[2].trim().toLowerCase();
            if (!arg.equals("all") && !arg.equals("*")) {
                try {
                    dropCount = Integer.parseInt(arg);
                } catch (NumberFormatException ex) {
                    return new BaritoneFacade.ExecutionResult("error", false, "invalid count value");
                }
                if (dropCount < 1 || dropCount > 64) {
                    return new BaritoneFacade.ExecutionResult("rejected", false, "count must be 1..64");
                }
                dropAll = false;
            }
        }

        Minecraft mc = Minecraft.getInstance();
        LocalPlayer self = mc.player;
        if (mc.gameMode == null || self == null) {
            return new BaritoneFacade.ExecutionResult("error", false, "player or game mode unavailable");
        }

        int sourceContainerSlot = playerInventorySlotToContainerSlot(sourceSlot);
        if (sourceContainerSlot < 0) {
            return new BaritoneFacade.ExecutionResult("error", false, "invalid source slot mapping");
        }

        try {
            self.closeContainer();
            int throwMode = dropAll ? 1 : 0;
            int throwsNeeded = dropAll ? 1 : dropCount;
            for (int i = 0; i < throwsNeeded; i++) {
                ItemStack before = self.getInventory().getItem(sourceSlot - 1).copy();
                if (before.isEmpty()) {
                    break;
                }
                mc.gameMode.handleInventoryMouseClick(
                        self.inventoryMenu.containerId,
                        sourceContainerSlot,
                        throwMode,
                        ClickType.THROW,
                        self
                );
                if (dropAll) {
                    break;
                }
                ItemStack after = self.getInventory().getItem(sourceSlot - 1);
                if (after.isEmpty()) {
                    break;
                }
            }
            return new BaritoneFacade.ExecutionResult("accepted", true, "");
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "drop failed: " + msg);
        }
    }

    private BaritoneFacade.ExecutionResult handleStoreToNearbyChest(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length < 1 || parts.length > 4) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.store_to_nearby_chest [radius_2_12] [max_stacks_1_36] [include_hotbar_0_or_1]"
            );
        }

        int radius = 6;
        int maxStacks = 24;
        boolean includeHotbar = false;
        if (parts.length >= 2) {
            try {
                radius = Integer.parseInt(parts[1]);
            } catch (NumberFormatException ex) {
                return new BaritoneFacade.ExecutionResult("error", false, "invalid radius value");
            }
        }
        if (parts.length >= 3) {
            try {
                maxStacks = Integer.parseInt(parts[2]);
            } catch (NumberFormatException ex) {
                return new BaritoneFacade.ExecutionResult("error", false, "invalid max_stacks value");
            }
        }
        if (parts.length >= 4) {
            String raw = parts[3].trim().toLowerCase(Locale.ROOT);
            if ("1".equals(raw) || "true".equals(raw)) {
                includeHotbar = true;
            } else if ("0".equals(raw) || "false".equals(raw)) {
                includeHotbar = false;
            } else {
                return new BaritoneFacade.ExecutionResult("error", false, "include_hotbar must be 0/1/false/true");
            }
        }
        if (radius < 2 || radius > 12) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "radius must be 2..12");
        }
        if (maxStacks < 1 || maxStacks > 36) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "max_stacks must be 1..36");
        }

        Minecraft mc = Minecraft.getInstance();
        LocalPlayer self = mc.player;
        if (mc.level == null || self == null || mc.gameMode == null) {
            return new BaritoneFacade.ExecutionResult("error", false, "player or game mode unavailable");
        }

        List<BlockPos> targets = findNearbyStorageBlocks(self, radius, 12);
        if (targets.isEmpty()) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "no nearby chest container found");
        }

        boolean opened = false;
        for (BlockPos pos : targets) {
            if (openStorageMenuAt(mc, self, pos)) {
                opened = true;
                break;
            }
        }
        if (!opened) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "unable to open nearby storage container");
        }

        int movedStacks = 0;
        try {
            AbstractContainerMenu menu = self.containerMenu;
            if (menu == null || menu instanceof InventoryMenu) {
                return new BaritoneFacade.ExecutionResult("rejected", false, "opened menu is not a storage container");
            }
            Map<Integer, Integer> playerSlotMap = playerInventoryMenuSlotMap(menu, self);
            if (playerSlotMap.isEmpty()) {
                return new BaritoneFacade.ExecutionResult("rejected", false, "storage menu has no mappable player inventory slots");
            }

            int startPlayerSlot = includeHotbar ? 1 : 10;
            for (int playerSlot = startPlayerSlot; playerSlot <= 36; playerSlot++) {
                int menuSlot = playerSlotMap.getOrDefault(playerSlot, -1);
                if (menuSlot < 0 || menuSlot >= menu.slots.size()) {
                    continue;
                }
                ItemStack before = menu.getSlot(menuSlot).getItem();
                if (before == null || before.isEmpty()) {
                    continue;
                }
                int beforeCount = before.getCount();
                String beforeItemId = itemId(before);
                mc.gameMode.handleInventoryMouseClick(menu.containerId, menuSlot, 0, ClickType.QUICK_MOVE, self);
                ItemStack after = menu.getSlot(menuSlot).getItem();
                int afterCount = after == null || after.isEmpty() ? 0 : after.getCount();
                String afterItemId = itemId(after);
                boolean moved = afterCount < beforeCount || !beforeItemId.equals(afterItemId);
                if (moved) {
                    movedStacks++;
                    if (movedStacks >= maxStacks) {
                        break;
                    }
                }
            }
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "store failed: " + msg);
        } finally {
            self.closeContainer();
        }

        if (movedStacks <= 0) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "no transferable inventory stacks");
        }
        return new BaritoneFacade.ExecutionResult("accepted", true, "");
    }

    private static Map<Integer, Integer> playerInventoryMenuSlotMap(AbstractContainerMenu menu, LocalPlayer self) {
        Map<Integer, Integer> out = new HashMap<>();
        if (menu == null || self == null) {
            return out;
        }
        for (int menuSlot = 0; menuSlot < menu.slots.size(); menuSlot++) {
            Slot slot = menu.slots.get(menuSlot);
            if (slot == null || slot.container != self.getInventory()) {
                continue;
            }
            int invIndex = slot.getContainerSlot();
            if (invIndex < 0 || invIndex >= 36) {
                continue;
            }
            out.putIfAbsent(invIndex + 1, menuSlot);
        }
        return out;
    }

    private static List<BlockPos> findNearbyStorageBlocks(LocalPlayer self, int radius, int maxCandidates) {
        BlockPos base = self.blockPosition();
        List<BlockPos> candidates = new ArrayList<>();
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -2; dy <= 2; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    BlockPos pos = base.offset(dx, dy, dz);
                    BlockState state = self.level().getBlockState(pos);
                    if (!isLikelyStorageBlock(state)) {
                        continue;
                    }
                    if (!state.hasBlockEntity()) {
                        continue;
                    }
                    candidates.add(pos.immutable());
                }
            }
        }
        candidates.sort(Comparator.comparingDouble(base::distSqr));
        if (candidates.size() > maxCandidates) {
            return new ArrayList<>(candidates.subList(0, maxCandidates));
        }
        return candidates;
    }

    private static boolean isLikelyStorageBlock(BlockState state) {
        if (state == null) {
            return false;
        }
        if (state.is(Blocks.CHEST)
                || state.is(Blocks.TRAPPED_CHEST)
                || state.is(Blocks.BARREL)
                || state.is(Blocks.ENDER_CHEST)
                || state.is(BlockTags.SHULKER_BOXES)) {
            return true;
        }
        String blockId = BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString().toLowerCase(Locale.ROOT);
        return blockId.contains("chest")
                || blockId.contains("barrel")
                || blockId.contains("crate")
                || blockId.contains("drawer")
                || blockId.contains("shulker");
    }

    private static boolean openStorageMenuAt(Minecraft mc, LocalPlayer self, BlockPos pos) {
        if (mc.gameMode == null) {
            return false;
        }
        self.closeContainer();
        for (int i = 0; i < 6; i++) {
            mc.gameMode.useItemOn(
                    self,
                    InteractionHand.MAIN_HAND,
                    new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false)
            );
            AbstractContainerMenu menu = self.containerMenu;
            if (!(menu instanceof InventoryMenu) && menu.slots.size() > 36) {
                return true;
            }
        }
        return false;
    }

    private BaritoneFacade.ExecutionResult handleFightHostile(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length > 3) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.fight_hostile [max_distance_1_24] [swings_1_6]"
            );
        }

        double maxDistance = 6.0;
        int swings = 2;
        if (parts.length >= 2) {
            try {
                maxDistance = Double.parseDouble(parts[1]);
            } catch (NumberFormatException ex) {
                return new BaritoneFacade.ExecutionResult("error", false, "invalid max distance");
            }
        }
        if (parts.length >= 3) {
            try {
                swings = Integer.parseInt(parts[2]);
            } catch (NumberFormatException ex) {
                return new BaritoneFacade.ExecutionResult("error", false, "invalid swings value");
            }
        }

        if (maxDistance < 1.0 || maxDistance > 24.0) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "max distance must be 1..24");
        }
        if (swings < 1 || swings > 6) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "swings must be 1..6");
        }

        Minecraft mc = Minecraft.getInstance();
        LocalPlayer self = mc.player;
        if (mc.level == null || self == null || mc.gameMode == null) {
            return new BaritoneFacade.ExecutionResult("error", false, "player or game mode unavailable");
        }

        Entity target = findNearestHostile(self, maxDistance);
        if (target == null) {
            return new BaritoneFacade.ExecutionResult("rejected", false, "no hostile in range");
        }

        try {
            faceEntity(self, target);
            for (int i = 0; i < swings && target.isAlive(); i++) {
                mc.gameMode.attack(self, target);
                self.swing(InteractionHand.MAIN_HAND);
            }
            return new BaritoneFacade.ExecutionResult("accepted", true, "");
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "fight failed: " + msg);
        }
    }

    private BaritoneFacade.ExecutionResult handleCraftItem(String commandLine) {
        String[] parts = commandLine.split("\\s+");
        if (parts.length < 2 || parts.length > 3) {
            return new BaritoneFacade.ExecutionResult(
                    "error",
                    false,
                    "usage: bridge.craft_item <item_id> [count_1_256]"
            );
        }

        String targetItemId = parts[1].trim().toLowerCase();
        if (targetItemId.isBlank()) {
            return new BaritoneFacade.ExecutionResult("error", false, "item id is required");
        }

        int targetCount = 1;
        if (parts.length == 3) {
            try {
                targetCount = Integer.parseInt(parts[2]);
            } catch (NumberFormatException ex) {
                return new BaritoneFacade.ExecutionResult("error", false, "invalid craft count");
            }
        }
        if (targetCount < 1 || targetCount > MAX_CRAFT_COMMAND_COUNT) {
            return new BaritoneFacade.ExecutionResult(
                    "rejected",
                    false,
                    "count must be between 1 and " + MAX_CRAFT_COMMAND_COUNT
            );
        }

        Minecraft mc = Minecraft.getInstance();
        LocalPlayer self = mc.player;
        if (mc.level == null || mc.gameMode == null || self == null) {
            return new BaritoneFacade.ExecutionResult("error", false, "player or game mode unavailable");
        }

        try {
            CraftAttemptResult attempt = craftItemFromOpenGrid(mc, self, targetItemId, targetCount);
            if (attempt.craftedCount() >= targetCount) {
                return new BaritoneFacade.ExecutionResult("accepted", true, "");
            }
            String detail = attempt.message() == null || attempt.message().isBlank()
                    ? "unable to craft target item"
                    : attempt.message();
            if (attempt.craftedCount() > 0) {
                detail = detail + " (crafted=" + attempt.craftedCount() + "/" + targetCount + ")";
            }
            return new BaritoneFacade.ExecutionResult("rejected", false, detail);
        } catch (Throwable ex) {
            String msg = ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage();
            return new BaritoneFacade.ExecutionResult("error", false, "craft failed: " + msg);
        }
    }

    private CraftAttemptResult craftItemFromOpenGrid(
            Minecraft mc,
            LocalPlayer self,
            String targetItemId,
            int targetCount
    ) {
        AbstractContainerMenu menu = self.containerMenu;
        GridSpec gridSpec = gridSpecForMenu(menu);
        if (gridSpec == null) {
            self.closeContainer();
            menu = self.containerMenu;
            gridSpec = gridSpecForMenu(menu);
        }
        if (gridSpec == null) {
            return new CraftAttemptResult(0, "unsupported menu for crafting; close open container");
        }

        boolean startedWithCraftingTable = menu instanceof CraftingMenu;
        boolean closeCraftingTableOnExit = false;
        try {
            CraftingRecipe recipe = chooseRecipeForOutput(self, menu, targetItemId, gridSpec);
            if (gridSpec.gridWidth() < 3
                    && shouldEscalateToCraftingTable(self, menu, targetItemId, gridSpec, recipe)) {
                CraftingMenuOpenState openState = ensureCraftingMenuReady(mc, self);
                if (openState == CraftingMenuOpenState.PENDING) {
                    return new CraftAttemptResult(0, "craft output pending");
                }
                if (openState == CraftingMenuOpenState.FAILED) {
                    return new CraftAttemptResult(0, "unable to open crafting table");
                }
                menu = self.containerMenu;
                gridSpec = gridSpecForMenu(menu);
                if (menu instanceof CraftingMenu && !startedWithCraftingTable) {
                    closeCraftingTableOnExit = true;
                }
                if (gridSpec != null) {
                    recipe = chooseRecipeForOutput(self, menu, targetItemId, gridSpec);
                }
            }
            if (recipe == null) {
                return new CraftAttemptResult(0, "no fitting crafting recipe in current grid");
            }

            List<IngredientPlacement> placements = buildPlacements(recipe, gridSpec);
            if (placements.isEmpty()) {
                return new CraftAttemptResult(0, "recipe has no placeable ingredients");
            }

            if (!stashCarriedStack(mc, self, menu, gridSpec)) {
                return new CraftAttemptResult(0, "unable to stash carried stack before crafting");
            }

            int craftedTotal = 0;
            for (int iter = 0; iter < targetCount; iter++) {
                if (!stashCarriedStack(mc, self, menu, gridSpec)) {
                    return new CraftAttemptResult(craftedTotal, "unable to stash carried stack before taking craft result");
                }
                int carriedOver = takeCraftResultOnce(mc, self, menu, gridSpec, targetItemId);
                if (carriedOver > 0) {
                    craftedTotal += carriedOver;
                    if (craftedTotal >= targetCount) {
                        return new CraftAttemptResult(craftedTotal, "");
                    }
                }
                if (isCraftGridOccupied(menu, gridSpec)) {
                    if (!clearCraftGrid(mc, self, menu, gridSpec)) {
                        return new CraftAttemptResult(craftedTotal, "craft output pending");
                    }
                }

                Map<Integer, Integer> sourceByGridSlot = findInventorySourceSlots(menu, gridSpec, placements);
                if (sourceByGridSlot == null) {
                    if (craftedTotal > 0) {
                        return new CraftAttemptResult(craftedTotal, "not enough ingredients for additional crafts");
                    }
                    return new CraftAttemptResult(0, "missing required ingredients");
                }

                for (IngredientPlacement placement : placements) {
                    int sourceSlot = sourceByGridSlot.getOrDefault(placement.gridSlotId(), -1);
                    if (sourceSlot < 0) {
                        return new CraftAttemptResult(craftedTotal, "missing mapped source slot");
                    }
                    if (!moveSingleItemToGrid(mc, self, menu, sourceSlot, placement.gridSlotId())) {
                        return new CraftAttemptResult(craftedTotal, "failed to place ingredient in crafting grid");
                    }
                }

                int produced = takeCraftResultWithSettle(mc, self, menu, gridSpec, targetItemId);
                if (produced <= 0) {
                    if (isCraftGridOccupied(menu, gridSpec)) {
                        return new CraftAttemptResult(craftedTotal, "craft output pending");
                    }
                    if (craftedTotal > 0) {
                        return new CraftAttemptResult(craftedTotal, "craft output unavailable for additional crafts");
                    }
                    return new CraftAttemptResult(0, "craft output unavailable");
                }
                craftedTotal += produced;
                if (craftedTotal >= targetCount) {
                    return new CraftAttemptResult(craftedTotal, "");
                }
            }
            return new CraftAttemptResult(craftedTotal, "");
        } finally {
            if (closeCraftingTableOnExit && self.containerMenu instanceof CraftingMenu) {
                self.closeContainer();
            }
        }
    }

    private GridSpec gridSpecForMenu(AbstractContainerMenu menu) {
        if (menu instanceof InventoryMenu) {
            return new GridSpec(0, new int[]{1, 2, 3, 4}, 2, 2, 9, 44);
        }
        if (menu instanceof CraftingMenu) {
            return new GridSpec(0, new int[]{1, 2, 3, 4, 5, 6, 7, 8, 9}, 3, 3, 10, 45);
        }
        return null;
    }

    private CraftingRecipe chooseRecipeForOutput(
            LocalPlayer self,
            AbstractContainerMenu menu,
            String targetItemId,
            GridSpec gridSpec
    ) {
        List<RecipeHolder<CraftingRecipe>> all = self.level().getRecipeManager().getAllRecipesFor(RecipeType.CRAFTING);
        CraftingRecipe selected = null;
        int selectedComplexity = Integer.MAX_VALUE;
        CraftingRecipe selectedCraftable = null;
        int selectedCraftableComplexity = Integer.MAX_VALUE;
        for (RecipeHolder<CraftingRecipe> holder : all) {
            CraftingRecipe recipe = holder.value();
            ItemStack resultStack = recipe.getResultItem(self.level().registryAccess());
            if (resultStack.isEmpty()) {
                continue;
            }
            if (!itemId(resultStack).equals(targetItemId)) {
                continue;
            }
            if (!recipeFitsGrid(recipe, gridSpec)) {
                continue;
            }
            int complexity = nonEmptyIngredientCount(recipe);
            if (selected == null || complexity < selectedComplexity) {
                selected = recipe;
                selectedComplexity = complexity;
            }
            List<IngredientPlacement> placements = buildPlacements(recipe, gridSpec);
            if (placements.isEmpty()) {
                continue;
            }
            if (findInventorySourceSlots(menu, gridSpec, placements) == null) {
                continue;
            }
            if (selectedCraftable == null || complexity < selectedCraftableComplexity) {
                selectedCraftable = recipe;
                selectedCraftableComplexity = complexity;
            }
        }
        return selectedCraftable != null ? selectedCraftable : selected;
    }

    private boolean shouldEscalateToCraftingTable(
            LocalPlayer self,
            AbstractContainerMenu menu,
            String targetItemId,
            GridSpec gridSpec,
            CraftingRecipe currentRecipe
    ) {
        if (gridSpec.gridWidth() >= 3) {
            return false;
        }
        if (currentRecipe == null) {
            return hasWiderRecipeForOutput(self, targetItemId, gridSpec);
        }
        if (isRecipeCraftableInGrid(menu, gridSpec, currentRecipe)) {
            return false;
        }
        return hasWiderRecipeForOutput(self, targetItemId, gridSpec);
    }

    private boolean hasWiderRecipeForOutput(LocalPlayer self, String targetItemId, GridSpec currentGridSpec) {
        List<RecipeHolder<CraftingRecipe>> all = self.level().getRecipeManager().getAllRecipesFor(RecipeType.CRAFTING);
        for (RecipeHolder<CraftingRecipe> holder : all) {
            CraftingRecipe recipe = holder.value();
            ItemStack resultStack = recipe.getResultItem(self.level().registryAccess());
            if (resultStack.isEmpty()) {
                continue;
            }
            if (!itemId(resultStack).equals(targetItemId)) {
                continue;
            }
            if (recipeFitsGrid(recipe, currentGridSpec)) {
                continue;
            }
            if (recipeRequiresWiderGrid(recipe, currentGridSpec)) {
                return true;
            }
        }
        return false;
    }

    private boolean recipeRequiresWiderGrid(CraftingRecipe recipe, GridSpec currentGridSpec) {
        if (recipe instanceof ShapedRecipe shaped) {
            return shaped.getWidth() > currentGridSpec.gridWidth()
                    || shaped.getHeight() > currentGridSpec.gridHeight();
        }
        return nonEmptyIngredientCount(recipe) > currentGridSpec.gridSlots().length;
    }

    private boolean isRecipeCraftableInGrid(
            AbstractContainerMenu menu,
            GridSpec gridSpec,
            CraftingRecipe recipe
    ) {
        if (!recipeFitsGrid(recipe, gridSpec)) {
            return false;
        }
        List<IngredientPlacement> placements = buildPlacements(recipe, gridSpec);
        if (placements.isEmpty()) {
            return false;
        }
        return findInventorySourceSlots(menu, gridSpec, placements) != null;
    }

    private CraftingMenuOpenState ensureCraftingMenuReady(Minecraft mc, LocalPlayer self) {
        if (self.containerMenu instanceof CraftingMenu) {
            return CraftingMenuOpenState.READY;
        }

        self.closeContainer();
        if (self.containerMenu instanceof CraftingMenu) {
            return CraftingMenuOpenState.READY;
        }

        BlockPos nearby = findNearbyCraftingTable(self, 5);
        if (nearby != null) {
            if (requestCraftingMenuOpen(mc, self, nearby)) {
                return CraftingMenuOpenState.PENDING;
            }
            return CraftingMenuOpenState.FAILED;
        }

        if (!placeCraftingTableNearby(mc, self)) {
            return CraftingMenuOpenState.FAILED;
        }
        return CraftingMenuOpenState.PENDING;
    }

    private static boolean requestCraftingMenuOpen(Minecraft mc, LocalPlayer self, BlockPos pos) {
        if (mc.gameMode == null) {
            return false;
        }
        mc.gameMode.useItemOn(
                self,
                InteractionHand.MAIN_HAND,
                new BlockHitResult(Vec3.atCenterOf(pos), Direction.UP, pos, false)
        );
        return true;
    }

    private BlockPos findNearbyCraftingTable(LocalPlayer self, int radius) {
        BlockPos base = self.blockPosition();
        BlockPos best = null;
        int bestDistSq = Integer.MAX_VALUE;
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dy = -2; dy <= 2; dy++) {
                for (int dz = -radius; dz <= radius; dz++) {
                    BlockPos pos = base.offset(dx, dy, dz);
                    if (!self.level().getBlockState(pos).is(Blocks.CRAFTING_TABLE)) {
                        continue;
                    }
                    int distSq = dx * dx + dy * dy + dz * dz;
                    if (distSq < bestDistSq) {
                        bestDistSq = distSq;
                        best = pos;
                    }
                }
            }
        }
        return best;
    }

    private boolean placeCraftingTableNearby(Minecraft mc, LocalPlayer self) {
        if (mc.gameMode == null) {
            return false;
        }
        self.closeContainer();
        AbstractContainerMenu menu = self.containerMenu;
        GridSpec gridSpec = gridSpecForMenu(menu);
        if (!(menu instanceof InventoryMenu) || gridSpec == null) {
            return false;
        }

        int tableSlot = findInventorySlotContaining(menu, gridSpec, "minecraft:crafting_table");
        if (tableSlot < 0) {
            return false;
        }

        int selectedHotbarButton = self.getInventory().selected;
        int selectedHotbarMenuSlot = 36 + selectedHotbarButton;
        if (tableSlot != selectedHotbarMenuSlot) {
            mc.gameMode.handleInventoryMouseClick(
                    menu.containerId,
                    tableSlot,
                    selectedHotbarButton,
                    ClickType.SWAP,
                    self
            );
        }

        BlockPos placePos = findSafeCraftingTablePlacement(self);
        if (placePos == null) {
            return false;
        }
        BlockPos supportPos = placePos.below();
        mc.gameMode.useItemOn(
                self,
                InteractionHand.MAIN_HAND,
                new BlockHitResult(Vec3.atCenterOf(supportPos), Direction.UP, supportPos, false)
        );

        return self.level().getBlockState(placePos).is(Blocks.CRAFTING_TABLE);
    }

    private static int findInventorySlotContaining(AbstractContainerMenu menu, GridSpec gridSpec, String itemId) {
        String target = itemId == null ? "" : itemId.trim().toLowerCase();
        if (target.isBlank()) {
            return -1;
        }
        for (int slotId = gridSpec.inventoryStart(); slotId <= gridSpec.inventoryEnd(); slotId++) {
            if (slotId < 0 || slotId >= menu.slots.size()) {
                continue;
            }
            ItemStack stack = menu.getSlot(slotId).getItem();
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            if (itemId(stack).equals(target)) {
                return slotId;
            }
        }
        return -1;
    }

    private static BlockPos findSafeCraftingTablePlacement(LocalPlayer self) {
        BlockPos base = self.blockPosition();
        for (Direction direction : new Direction[]{Direction.NORTH, Direction.SOUTH, Direction.WEST, Direction.EAST}) {
            BlockPos placePos = base.relative(direction);
            if (!self.level().getBlockState(placePos).canBeReplaced()) {
                continue;
            }
            BlockPos supportPos = placePos.below();
            if (self.level().getBlockState(supportPos).isAir()) {
                continue;
            }
            return placePos;
        }
        return null;
    }

    private static boolean recipeFitsGrid(CraftingRecipe recipe, GridSpec gridSpec) {
        if (recipe instanceof ShapedRecipe shaped) {
            return shaped.getWidth() <= gridSpec.gridWidth() && shaped.getHeight() <= gridSpec.gridHeight();
        }
        return nonEmptyIngredientCount(recipe) <= gridSpec.gridSlots().length;
    }

    private static int nonEmptyIngredientCount(CraftingRecipe recipe) {
        int count = 0;
        for (Ingredient ingredient : recipe.getIngredients()) {
            if (ingredient != null && !ingredient.isEmpty()) {
                count++;
            }
        }
        return count;
    }

    private List<IngredientPlacement> buildPlacements(CraftingRecipe recipe, GridSpec gridSpec) {
        List<IngredientPlacement> placements = new ArrayList<>();
        if (recipe instanceof ShapedRecipe shaped) {
            int width = shaped.getWidth();
            int height = shaped.getHeight();
            List<Ingredient> ingredients = recipe.getIngredients();
            for (int row = 0; row < height; row++) {
                for (int col = 0; col < width; col++) {
                    int recipeIndex = row * width + col;
                    if (recipeIndex < 0 || recipeIndex >= ingredients.size()) {
                        continue;
                    }
                    Ingredient ingredient = ingredients.get(recipeIndex);
                    if (ingredient == null || ingredient.isEmpty()) {
                        continue;
                    }
                    int gridIndex = row * gridSpec.gridWidth() + col;
                    if (gridIndex < 0 || gridIndex >= gridSpec.gridSlots().length) {
                        continue;
                    }
                    placements.add(new IngredientPlacement(gridSpec.gridSlots()[gridIndex], ingredient));
                }
            }
            return placements;
        }

        int nextGridIndex = 0;
        for (Ingredient ingredient : recipe.getIngredients()) {
            if (ingredient == null || ingredient.isEmpty()) {
                continue;
            }
            if (nextGridIndex >= gridSpec.gridSlots().length) {
                return List.of();
            }
            placements.add(new IngredientPlacement(gridSpec.gridSlots()[nextGridIndex], ingredient));
            nextGridIndex++;
        }
        return placements;
    }

    private Map<Integer, Integer> findInventorySourceSlots(
            AbstractContainerMenu menu,
            GridSpec gridSpec,
            List<IngredientPlacement> placements
    ) {
        Map<Integer, Integer> usageBySourceSlot = new HashMap<>();
        Map<Integer, Integer> sourceByGridSlot = new HashMap<>();
        for (IngredientPlacement placement : placements) {
            int sourceSlot = -1;
            for (int slotId = gridSpec.inventoryStart(); slotId <= gridSpec.inventoryEnd(); slotId++) {
                if (slotId < 0 || slotId >= menu.slots.size()) {
                    continue;
                }
                ItemStack stack = menu.getSlot(slotId).getItem();
                if (stack == null || stack.isEmpty()) {
                    continue;
                }
                if (!placement.ingredient().test(stack)) {
                    continue;
                }
                int alreadyReserved = usageBySourceSlot.getOrDefault(slotId, 0);
                if (stack.getCount() <= alreadyReserved) {
                    continue;
                }
                sourceSlot = slotId;
                break;
            }
            if (sourceSlot < 0) {
                return null;
            }
            usageBySourceSlot.put(sourceSlot, usageBySourceSlot.getOrDefault(sourceSlot, 0) + 1);
            sourceByGridSlot.put(placement.gridSlotId(), sourceSlot);
        }
        return sourceByGridSlot;
    }

    private static boolean moveSingleItemToGrid(
            Minecraft mc,
            LocalPlayer self,
            AbstractContainerMenu menu,
            int sourceSlot,
            int targetGridSlot
    ) {
        int before = menu.getSlot(targetGridSlot).getItem().getCount();
        mc.gameMode.handleInventoryMouseClick(menu.containerId, sourceSlot, 0, ClickType.PICKUP, self);
        mc.gameMode.handleInventoryMouseClick(menu.containerId, targetGridSlot, 1, ClickType.PICKUP, self);
        if (!menu.getCarried().isEmpty()) {
            mc.gameMode.handleInventoryMouseClick(menu.containerId, sourceSlot, 0, ClickType.PICKUP, self);
        }
        int after = menu.getSlot(targetGridSlot).getItem().getCount();
        return after > before;
    }

    private static boolean clearCraftGrid(
            Minecraft mc,
            LocalPlayer self,
            AbstractContainerMenu menu,
            GridSpec gridSpec
    ) {
        for (int gridSlot : gridSpec.gridSlots()) {
            if (gridSlot < 0 || gridSlot >= menu.slots.size()) {
                return false;
            }
            ItemStack stack = menu.getSlot(gridSlot).getItem();
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            mc.gameMode.handleInventoryMouseClick(menu.containerId, gridSlot, 0, ClickType.QUICK_MOVE, self);
        }
        for (int gridSlot : gridSpec.gridSlots()) {
            ItemStack stack = menu.getSlot(gridSlot).getItem();
            if (stack != null && !stack.isEmpty()) {
                return false;
            }
        }
        return stashCarriedStack(mc, self, menu, gridSpec);
    }

    private static int takeCraftResultOnce(
            Minecraft mc,
            LocalPlayer self,
            AbstractContainerMenu menu,
            GridSpec gridSpec,
            String targetItemId
    ) {
        if (gridSpec.resultSlot() < 0 || gridSpec.resultSlot() >= menu.slots.size()) {
            return 0;
        }
        ItemStack resultBefore = menu.getSlot(gridSpec.resultSlot()).getItem();
        if (resultBefore == null || resultBefore.isEmpty()) {
            return 0;
        }
        if (!itemId(resultBefore).equals(targetItemId)) {
            return 0;
        }

        if (!stashCarriedStack(mc, self, menu, gridSpec)) {
            return 0;
        }

        int beforeTotal = countTargetItemInStorage(menu, gridSpec, targetItemId);
        mc.gameMode.handleInventoryMouseClick(menu.containerId, gridSpec.resultSlot(), 0, ClickType.QUICK_MOVE, self);
        if (!menu.getCarried().isEmpty() && !stashCarriedStack(mc, self, menu, gridSpec)) {
            return 0;
        }
        int afterTotal = countTargetItemInStorage(menu, gridSpec, targetItemId);
        int quickMoved = Math.max(0, afterTotal - beforeTotal);
        if (quickMoved > 0) {
            return quickMoved;
        }

        mc.gameMode.handleInventoryMouseClick(menu.containerId, gridSpec.resultSlot(), 0, ClickType.PICKUP, self);
        ItemStack carried = menu.getCarried();
        if (carried == null || carried.isEmpty()) {
            return 0;
        }
        if (!itemId(carried).equals(targetItemId)) {
            stashCarriedStack(mc, self, menu, gridSpec);
            return 0;
        }

        if (!stashCarriedStack(mc, self, menu, gridSpec)) {
            return 0;
        }
        int produced = Math.max(0, countTargetItemInStorage(menu, gridSpec, targetItemId) - afterTotal);
        if (produced <= 0) {
            ItemStack resultAfter = menu.getSlot(gridSpec.resultSlot()).getItem();
            ItemStack carriedAfter = menu.getCarried();
            SB4BaritoneBridgeMod.LOGGER.info(
                    "[{}] craft_result_take_zero target={} resultBefore={}x{} resultAfter={}x{} carriedAfter={}x{} beforeTotal={} afterTotal={} menuId={}",
                    SB4BaritoneBridgeMod.MODID,
                    targetItemId,
                    itemId(resultBefore),
                    resultBefore.getCount(),
                    itemId(resultAfter),
                    resultAfter == null ? 0 : resultAfter.getCount(),
                    itemId(carriedAfter),
                    carriedAfter == null ? 0 : carriedAfter.getCount(),
                    beforeTotal,
                    afterTotal,
                    menu.containerId
            );
        }
        return produced;
    }

    private static int takeCraftResultWithSettle(
            Minecraft mc,
            LocalPlayer self,
            AbstractContainerMenu menu,
            GridSpec gridSpec,
            String targetItemId
    ) {
        int produced = takeCraftResultOnce(mc, self, menu, gridSpec, targetItemId);
        if (produced > 0) {
            return produced;
        }

        for (int attempt = 0; attempt < CRAFT_RESULT_SETTLE_ATTEMPTS; attempt++) {
            if (!isCraftGridOccupied(menu, gridSpec)) {
                return 0;
            }
            try {
                Thread.sleep(CRAFT_RESULT_SETTLE_SLEEP_MS);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
                return 0;
            }
            produced = takeCraftResultOnce(mc, self, menu, gridSpec, targetItemId);
            if (produced > 0) {
                return produced;
            }
        }
        return 0;
    }

    private static boolean isCraftGridOccupied(AbstractContainerMenu menu, GridSpec gridSpec) {
        for (int gridSlot : gridSpec.gridSlots()) {
            if (gridSlot < 0 || gridSlot >= menu.slots.size()) {
                continue;
            }
            ItemStack stack = menu.getSlot(gridSlot).getItem();
            if (stack != null && !stack.isEmpty()) {
                return true;
            }
        }
        return false;
    }

    private static boolean stashCarriedStack(
            Minecraft mc,
            LocalPlayer self,
            AbstractContainerMenu menu,
            GridSpec gridSpec
    ) {
        ItemStack carried = menu.getCarried();
        if (carried == null || carried.isEmpty()) {
            return true;
        }

        for (int slotId = gridSpec.inventoryStart(); slotId <= gridSpec.inventoryEnd(); slotId++) {
            if (slotId < 0 || slotId >= menu.slots.size()) {
                continue;
            }
            ItemStack inSlot = menu.getSlot(slotId).getItem();
            if (inSlot == null || inSlot.isEmpty()) {
                mc.gameMode.handleInventoryMouseClick(menu.containerId, slotId, 0, ClickType.PICKUP, self);
            } else if (ItemStack.isSameItemSameComponents(inSlot, menu.getCarried())
                    && inSlot.getCount() < inSlot.getMaxStackSize()) {
                mc.gameMode.handleInventoryMouseClick(menu.containerId, slotId, 0, ClickType.PICKUP, self);
            }
            if (menu.getCarried().isEmpty()) {
                return true;
            }
        }
        return menu.getCarried().isEmpty();
    }

    private static int countTargetItemInStorage(
            AbstractContainerMenu menu,
            GridSpec gridSpec,
            String targetItemId
    ) {
        String target = targetItemId == null ? "" : targetItemId.trim().toLowerCase();
        if (target.isBlank()) {
            return 0;
        }

        int total = 0;
        for (int slotId = gridSpec.inventoryStart(); slotId <= gridSpec.inventoryEnd(); slotId++) {
            if (slotId < 0 || slotId >= menu.slots.size()) {
                continue;
            }
            ItemStack stack = menu.getSlot(slotId).getItem();
            if (stack == null || stack.isEmpty()) {
                continue;
            }
            if (itemId(stack).equals(target)) {
                total += stack.getCount();
            }
        }

        ItemStack carried = menu.getCarried();
        if (carried != null && !carried.isEmpty() && itemId(carried).equals(target)) {
            total += carried.getCount();
        }
        return total;
    }

    private static String itemId(ItemStack stack) {
        if (stack == null || stack.isEmpty()) {
            return "";
        }
        return BuiltInRegistries.ITEM.getKey(stack.getItem()).toString().toLowerCase();
    }

    private static Entity findNearestHostile(LocalPlayer self, double maxDistance) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.level == null) {
            return null;
        }
        double maxDistanceSq = maxDistance * maxDistance;
        double bestDistanceSq = maxDistanceSq;
        Entity nearest = null;
        for (Entity entity : mc.level.entitiesForRendering()) {
            if (!(entity instanceof Monster monster) || !monster.isAlive()) {
                continue;
            }
            double d2 = self.distanceToSqr(monster);
            if (d2 < bestDistanceSq) {
                bestDistanceSq = d2;
                nearest = monster;
            }
        }
        return nearest;
    }

    private static void faceEntity(LocalPlayer self, Entity target) {
        double dx = target.getX() - self.getX();
        double dy = target.getEyeY() - self.getEyeY();
        double dz = target.getZ() - self.getZ();
        double horizontal = Math.sqrt(dx * dx + dz * dz);
        float yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
        float pitch = (float) (-Math.toDegrees(Math.atan2(dy, Math.max(1.0E-6, horizontal))));
        self.setYRot(yaw);
        self.setXRot(pitch);
    }

    private static boolean isEdibleStack(ItemStack stack) {
        if (stack == null || stack.isEmpty()) {
            return false;
        }
        UseAnim useAnim = stack.getUseAnimation();
        return useAnim == UseAnim.EAT || useAnim == UseAnim.DRINK;
    }

    private static int keyCount(Map<String, Integer> counts, String itemId) {
        if (counts == null || itemId == null) {
            return 0;
        }
        return Math.max(0, counts.getOrDefault(itemId.toLowerCase(Locale.ROOT), 0));
    }

    private static String inferStoneblockStageHint(Map<String, Integer> counts) {
        if (counts == null || counts.isEmpty()) {
            return "bootstrap";
        }
        int craftingTable = keyCount(counts, "minecraft:crafting_table");
        int furnace = keyCount(counts, "minecraft:furnace");
        int cobble = keyCount(counts, "minecraft:cobblestone") + keyCount(counts, "minecraft:cobbled_deepslate");
        int gravel = keyCount(counts, "minecraft:gravel");
        int sand = keyCount(counts, "minecraft:sand");
        int dirt = keyCount(counts, "minecraft:dirt");
        int hammer = keyCount(counts, "ftbstuff:stone_hammer")
                + keyCount(counts, "ftbstuff:iron_hammer")
                + keyCount(counts, "ftbstuff:diamond_hammer");
        int brush = keyCount(counts, "ftbstuff:iron_brush");

        if (craftingTable <= 0) {
            return "bootstrap_crafting";
        }
        if (hammer <= 0) {
            return "bootstrap_hammer";
        }
        if ((gravel + sand + dirt) < 24) {
            return "hammer_to_resources";
        }
        if (furnace <= 0) {
            return "early_smelting";
        }
        if (brush <= 0) {
            return "pre_sieving";
        }
        if (cobble >= 256) {
            return "resource_stable";
        }
        return "mid_progression";
    }

    private static String screenTitle(Screen screen) {
        if (screen == null || screen.getTitle() == null) {
            return "";
        }
        return nonNullText(screen.getTitle().getString());
    }

    private static String clientStateHint(Minecraft mc, boolean inWorld, Screen screen) {
        if (mc == null) {
            return "client_unavailable";
        }

        String screenClass = screen == null ? "" : screen.getClass().getSimpleName().toLowerCase(Locale.ROOT);
        String title = screenTitle(screen).toLowerCase(Locale.ROOT);

        if (inWorld) {
            if (screen == null) {
                return "in_world";
            }
            if (screen.isPauseScreen() || screenClass.contains("pause")) {
                return "paused_in_world";
            }
            return "in_world_screen";
        }

        if (screen == null) {
            return "loading_or_transition";
        }
        if (screenClass.contains("title")) {
            return "main_menu";
        }
        if (screenClass.contains("pause")) {
            return "paused_menu";
        }
        if (screenClass.contains("receiving")
                || screenClass.contains("loading")
                || screenClass.contains("progress")
                || screenClass.contains("connect")
                || title.contains("loading")
                || title.contains("joining")
                || title.contains("connecting")
                || title.contains("receiving")) {
            return "loading_or_joining";
        }
        return "menu_not_in_world";
    }

    private List<Path> listInboxFiles() {
        if (!Files.isDirectory(inboxDir)) {
            return List.of();
        }
        List<Path> files = new ArrayList<>();
        try (DirectoryStream<Path> stream = Files.newDirectoryStream(inboxDir, "*.json")) {
            for (Path p : stream) {
                files.add(p);
            }
        } catch (IOException ex) {
            SB4BaritoneBridgeMod.LOGGER.error("[{}] failed to list inbox files", SB4BaritoneBridgeMod.MODID, ex);
            return List.of();
        }

        files.sort(
                Comparator
                        .comparingLong(this::safeLastModifiedMillis)
                        .thenComparing(path -> path.getFileName().toString())
        );
        return files;
    }

    private long safeLastModifiedMillis(Path path) {
        try {
            return Files.getLastModifiedTime(path).toMillis();
        } catch (IOException ex) {
            return Long.MAX_VALUE;
        }
    }

    private BridgeCommand readCommand(Path path) throws IOException {
        try (Reader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
            BridgeCommand cmd = gson.fromJson(r, BridgeCommand.class);
            if (cmd == null) {
                throw new IOException("empty JSON object");
            }
            if (cmd.id == null || cmd.id.isBlank()) {
                cmd.id = path.getFileName().toString().replace(".json", "");
            }
            cmd.id = normalizeId(cmd.id);
            if (cmd.command == null || cmd.command.isBlank()) {
                throw new IOException("missing command");
            }
            if (cmd.createdAtMs == 0L) {
                cmd.createdAtMs = System.currentTimeMillis();
            }
            if (cmd.trackActive == null) {
                cmd.trackActive = Boolean.TRUE;
            }
            return cmd;
        }
    }

    private void writeMalformedResponse(Path sourcePath, Exception ex) {
        BridgeResponse response = new BridgeResponse();
        response.id = normalizeId(sourcePath.getFileName().toString().replace(".json", ""));
        response.command = "";
        response.status = "error";
        response.accepted = false;
        response.error = "malformed command: " + ex.getMessage();
        response.createdAtMs = 0L;
        response.processedAtMs = System.currentTimeMillis();
        response.bridgeVersion = BRIDGE_VERSION;
        recordCommandFailure(response.id, response.command, response.status, response.error);
        writeOrQueueResponse(response);
        rememberRecentResponse(response);
    }

    private void maybeWriteStatus() {
        long now = System.currentTimeMillis();
        if (now - lastStatusWriteMs < STATUS_WRITE_INTERVAL_MS) {
            return;
        }
        lastStatusWriteMs = now;

        BaritoneFacade.StatusSnapshot snapshot = baritoneFacade.snapshot();
        Minecraft mc = Minecraft.getInstance();

        BridgeStatus status = new BridgeStatus();
        status.bridgeOk = true;
        status.statusSchemaVersion = STATUS_SCHEMA_VERSION;
        status.bridgeVersion = BRIDGE_VERSION;
        status.modpackName = MODPACK_NAME;
        status.timestampMs = now;
        status.timestampIso = Instant.ofEpochMilli(now).toString();
        status.bridgeWriterPid = bridgeWriterPid;
        status.bridgeWriterSessionId = bridgeWriterSessionId;
        status.bridgeWriterStatusSeq = ++statusWriteSequence;
        status.inWorld = mc.level != null && mc.player != null;
        Screen currentScreen = mc.screen;
        status.windowActive = mc.isWindowActive();
        status.currentScreen = currentScreen != null ? currentScreen.getClass().getSimpleName() : "";
        status.screenName = status.currentScreen;
        status.screenClass = currentScreen != null ? currentScreen.getClass().getName() : "";
        status.screenTitle = screenTitle(currentScreen);
        status.screenPausesGame = currentScreen != null && currentScreen.isPauseScreen();
        status.clientStateHint = clientStateHint(mc, status.inWorld, currentScreen);
        status.playerName = mc.player != null ? mc.player.getName().getString() : "";
        status.playerUuid = mc.player != null ? mc.player.getStringUUID() : "";
        status.dimensionId = mc.level != null ? mc.level.dimension().location().toString() : "";
        status.gameMode = mc.gameMode != null ? String.valueOf(mc.gameMode.getPlayerMode()) : "";
        status.worldTime = mc.level != null ? mc.level.getGameTime() : 0L;
        status.dayTime = mc.level != null ? mc.level.getDayTime() : 0L;
        status.baritoneLoaded = snapshot.loaded;
        status.baritoneError = snapshot.errorMessage;
        status.isPathing = snapshot.isPathing;
        status.currentGoal = snapshot.goal;
        status.lastCommandId = lastCommandId;
        status.lastCommand = lastCommand;
        status.lastCommandResult = lastCommandResult;
        status.lastCommandError = lastCommandError;
        status.lastCommandAtMs = lastCommandAtMs;
        status.queueDepth = listInboxFiles().size();
        status.pendingResponseCount = pendingResponses.size();
        status.commandFailureWarningMetrics = snapshotCommandFailureWarningMetrics();
        status.bridgeCapabilities = BRIDGE_CAPABILITIES;
        status.ultimineKeybindPresent = false;
        status.ultimineActive = false;
        status.mayfly = false;
        status.flying = false;

        KeyMapping ultimineKey = findUltimineKeyMapping(mc);
        if (ultimineKey != null) {
            status.ultimineKeybindPresent = true;
            status.ultimineActive = ultimineKey.isDown();
        }

        status.health = 0.0F;
        status.maxHealth = 0.0F;
        status.foodLevel = 0;
        status.saturation = 0.0F;
        status.airSupply = 0;
        status.onFire = false;
        status.inLava = false;
        status.inWater = false;
        status.posX = 0.0;
        status.posY = 0.0;
        status.posZ = 0.0;
        status.nearestOtherPlayerCount = 0;
        status.nearestOtherPlayerName = "";
        status.nearestOtherPlayerDistance = -1.0;
        status.nearbyHostileCount = 0;
        status.nearestHostileType = "";
        status.nearestHostileDistance = -1.0;
        status.selectedHotbarSlot = -1;
        status.mainHandItem = "";
        status.mainHandDamage = -1;
        status.mainHandMaxDamage = -1;
        status.mainHandRemainingDurability = -1;
        status.offhandItem = "";
        status.offhandCount = 0;
        status.offhandEdible = false;
        status.deadOrDying = false;
        status.crouching = false;
        status.fallDistance = 0.0F;
        status.inventoryFreeSlots = 0;
        status.stoneblockStageHint = "not_in_world";
        status.stoneblockKeyItemCounts = new HashMap<>();
        status.hotbar = new ArrayList<>();
        status.inventory = new ArrayList<>();

        if (status.inWorld) {
            LocalPlayer self = mc.player;
            if (self != null) {
                FoodData foodData = self.getFoodData();
                status.health = self.getHealth();
                status.maxHealth = self.getMaxHealth();
                status.foodLevel = foodData.getFoodLevel();
                status.saturation = foodData.getSaturationLevel();
                status.airSupply = self.getAirSupply();
                status.onFire = self.isOnFire();
                status.inLava = self.isInLava();
                status.inWater = self.isInWater();
                status.posX = self.getX();
                status.posY = self.getY();
                status.posZ = self.getZ();
                status.deadOrDying = self.isDeadOrDying();
                status.crouching = self.isCrouching();
                status.fallDistance = self.fallDistance;
                status.mayfly = self.getAbilities().mayfly;
                status.flying = self.getAbilities().flying;
                status.selectedHotbarSlot = self.getInventory().selected + 1;

                ItemStack mainHand = self.getMainHandItem();
                if (!mainHand.isEmpty()) {
                    status.mainHandItem = BuiltInRegistries.ITEM.getKey(mainHand.getItem()).toString();
                    if (mainHand.isDamageableItem()) {
                        status.mainHandDamage = mainHand.getDamageValue();
                        status.mainHandMaxDamage = mainHand.getMaxDamage();
                        status.mainHandRemainingDurability = status.mainHandMaxDamage - status.mainHandDamage;
                    }
                }

                ItemStack offHand = self.getOffhandItem();
                if (!offHand.isEmpty()) {
                    status.offhandItem = BuiltInRegistries.ITEM.getKey(offHand.getItem()).toString();
                    status.offhandCount = offHand.getCount();
                    status.offhandEdible = isEdibleStack(offHand);
                    String key = status.offhandItem.toLowerCase(Locale.ROOT);
                    if (STONEBLOCK_KEY_ITEM_IDS.contains(key)) {
                        status.stoneblockKeyItemCounts.merge(key, status.offhandCount, Integer::sum);
                    }
                }

                for (int i = 0; i < 9; i++) {
                    ItemStack stack = self.getInventory().getItem(i);
                    HotbarEntry entry = new HotbarEntry();
                    entry.slot = i + 1;
                    if (stack.isEmpty()) {
                        entry.itemId = "";
                        entry.count = 0;
                        entry.edible = false;
                        entry.damage = -1;
                        entry.maxDamage = -1;
                        entry.remainingDurability = -1;
                    } else {
                        entry.itemId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                        entry.count = stack.getCount();
                        entry.edible = isEdibleStack(stack);
                        if (stack.isDamageableItem()) {
                            entry.damage = stack.getDamageValue();
                            entry.maxDamage = stack.getMaxDamage();
                            entry.remainingDurability = entry.maxDamage - entry.damage;
                        } else {
                            entry.damage = -1;
                            entry.maxDamage = -1;
                            entry.remainingDurability = -1;
                        }
                    }
                    status.hotbar.add(entry);
                }

                for (int i = 0; i < 36; i++) {
                    ItemStack stack = self.getInventory().getItem(i);
                    InventoryEntry entry = new InventoryEntry();
                    entry.slot = i + 1;
                    entry.containerSlot = playerInventorySlotToContainerSlot(i + 1);
                    entry.inHotbar = i < 9;
                    if (stack.isEmpty()) {
                        entry.itemId = "";
                        entry.count = 0;
                        entry.edible = false;
                        entry.damage = -1;
                        entry.maxDamage = -1;
                        entry.remainingDurability = -1;
                    } else {
                        entry.itemId = BuiltInRegistries.ITEM.getKey(stack.getItem()).toString();
                        entry.count = stack.getCount();
                        entry.edible = isEdibleStack(stack);
                        if (stack.isDamageableItem()) {
                            entry.damage = stack.getDamageValue();
                            entry.maxDamage = stack.getMaxDamage();
                            entry.remainingDurability = entry.maxDamage - entry.damage;
                        } else {
                            entry.damage = -1;
                            entry.maxDamage = -1;
                            entry.remainingDurability = -1;
                        }
                        String key = entry.itemId.toLowerCase(Locale.ROOT);
                        if (STONEBLOCK_KEY_ITEM_IDS.contains(key)) {
                            status.stoneblockKeyItemCounts.merge(key, entry.count, Integer::sum);
                        }
                    }
                    status.inventory.add(entry);
                }

                int freeSlots = 0;
                for (InventoryEntry entry : status.inventory) {
                    if (entry.itemId == null || entry.itemId.isBlank() || entry.count <= 0) {
                        freeSlots++;
                    }
                }
                status.inventoryFreeSlots = freeSlots;
                status.stoneblockStageHint = inferStoneblockStageHint(status.stoneblockKeyItemCounts);

                int otherCount = 0;
                double bestDistanceSq = Double.MAX_VALUE;
                String nearestName = "";
                if (mc.level != null) {
                    for (AbstractClientPlayer other : mc.level.players()) {
                        if (other == null || other.getUUID().equals(self.getUUID())) {
                            continue;
                        }
                        otherCount++;
                        double d2 = self.distanceToSqr(other);
                        if (d2 < bestDistanceSq) {
                            bestDistanceSq = d2;
                            nearestName = other.getGameProfile().getName();
                        }
                    }
                }
                status.nearestOtherPlayerCount = otherCount;
                status.nearestOtherPlayerName = nearestName;
                status.nearestOtherPlayerDistance = bestDistanceSq == Double.MAX_VALUE ? -1.0 : Math.sqrt(bestDistanceSq);

                int hostileCount = 0;
                double bestHostileDistanceSq = Double.MAX_VALUE;
                String nearestHostileType = "";
                if (mc.level != null) {
                    for (Entity entity : mc.level.entitiesForRendering()) {
                        if (!(entity instanceof Monster monster) || !monster.isAlive()) {
                            continue;
                        }
                        double d2 = self.distanceToSqr(monster);
                        if (d2 > 64.0 * 64.0) {
                            continue;
                        }
                        hostileCount++;
                        if (d2 < bestHostileDistanceSq) {
                            bestHostileDistanceSq = d2;
                            nearestHostileType = monster.getType().toString();
                        }
                    }
                }
                status.nearbyHostileCount = hostileCount;
                status.nearestHostileType = nearestHostileType;
                status.nearestHostileDistance = bestHostileDistanceSq == Double.MAX_VALUE ? -1.0 : Math.sqrt(bestHostileDistanceSq);
            }
        }
        writeJson(statusPath, status);
    }

    private boolean writeJson(Path path, Object payload) {
        String json = gson.toJson(payload);
        IOException lastError = null;
        for (int attempt = 1; attempt <= 3; attempt++) {
            Path tempPath = path.resolveSibling(path.getFileName().toString() + ".tmp");
            try (Writer w = Files.newBufferedWriter(
                    tempPath,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE
            )) {
                w.write(json);
                w.flush();
                try {
                    Files.move(
                            tempPath,
                            path,
                            StandardCopyOption.REPLACE_EXISTING,
                            StandardCopyOption.ATOMIC_MOVE
                    );
                } catch (AtomicMoveNotSupportedException ignored) {
                    Files.move(tempPath, path, StandardCopyOption.REPLACE_EXISTING);
                }
                return true;
            } catch (IOException ex) {
                lastError = ex;
                deleteQuietly(tempPath);
            }
        }
        if (lastError != null) {
            SB4BaritoneBridgeMod.LOGGER.error("[{}] failed writing JSON to {}", SB4BaritoneBridgeMod.MODID, path, lastError);
        }
        return false;
    }

    private void deleteQuietly(Path path) {
        try {
            Files.deleteIfExists(path);
        } catch (IOException ex) {
            SB4BaritoneBridgeMod.LOGGER.warn("[{}] failed deleting {}", SB4BaritoneBridgeMod.MODID, path, ex);
        }
    }

    private enum CraftingMenuOpenState {
        READY,
        PENDING,
        FAILED
    }

    private record GridSpec(
            int resultSlot,
            int[] gridSlots,
            int gridWidth,
            int gridHeight,
            int inventoryStart,
            int inventoryEnd
    ) {
    }

    private record IngredientPlacement(int gridSlotId, Ingredient ingredient) {
    }

    private record CraftAttemptResult(int craftedCount, String message) {
    }

    private static final class BridgeCommand {
        String id;
        String command;
        long createdAtMs;
        Boolean trackActive;
    }

    private static final class BridgeResponse {
        String id;
        String command;
        String status;
        boolean accepted;
        String error;
        long createdAtMs;
        long processedAtMs;
        boolean trackActive;
        String bridgeVersion;
    }

    private static final class BridgeStatus {
        boolean bridgeOk;
        int statusSchemaVersion;
        String bridgeVersion;
        String modpackName;
        long timestampMs;
        String timestampIso;
        long bridgeWriterPid;
        String bridgeWriterSessionId;
        long bridgeWriterStatusSeq;
        boolean inWorld;
        boolean windowActive;
        String currentScreen;
        String screenName;
        String screenClass;
        String screenTitle;
        boolean screenPausesGame;
        String clientStateHint;
        String playerName;
        String playerUuid;
        String dimensionId;
        String gameMode;
        long worldTime;
        long dayTime;
        boolean baritoneLoaded;
        String baritoneError;
        boolean isPathing;
        String currentGoal;
        String lastCommandId;
        String lastCommand;
        String lastCommandResult;
        String lastCommandError;
        long lastCommandAtMs;
        int queueDepth;
        int pendingResponseCount;
        CommandFailureWarningMetrics commandFailureWarningMetrics;
        List<String> bridgeCapabilities;
        boolean ultimineKeybindPresent;
        boolean ultimineActive;
        boolean mayfly;
        boolean flying;
        float health;
        float maxHealth;
        int foodLevel;
        float saturation;
        int airSupply;
        boolean onFire;
        boolean inLava;
        boolean inWater;
        double posX;
        double posY;
        double posZ;
        int nearestOtherPlayerCount;
        String nearestOtherPlayerName;
        double nearestOtherPlayerDistance;
        int nearbyHostileCount;
        String nearestHostileType;
        double nearestHostileDistance;
        int selectedHotbarSlot;
        String mainHandItem;
        int mainHandDamage;
        int mainHandMaxDamage;
        int mainHandRemainingDurability;
        String offhandItem;
        int offhandCount;
        boolean offhandEdible;
        boolean deadOrDying;
        boolean crouching;
        float fallDistance;
        int inventoryFreeSlots;
        String stoneblockStageHint;
        Map<String, Integer> stoneblockKeyItemCounts;
        List<HotbarEntry> hotbar;
        List<InventoryEntry> inventory;
    }

    private static final class CommandFailureReasonCounter {
        long failureCount;
        long warningCount;
        long suppressedWarningCount;
        long suppressedSinceLastWarn;
        long lastFailureAtMs;
        long lastWarnAtMs;
        String lastStatus = "";
        String lastError = "";
    }

    private static final class CommandFailureWarningMetrics {
        long warnIntervalMs;
        long totalFailures;
        long totalWarningsEmitted;
        long totalWarningsSuppressed;
        Map<String, CommandFailureReasonMetrics> byReason;
    }

    private static final class CommandFailureReasonMetrics {
        long failureCount;
        long warningCount;
        long suppressedWarningCount;
        long suppressedSinceLastWarning;
        long lastFailureAtMs;
        long lastWarningAtMs;
        String lastStatus;
        String lastError;
    }

    private static final class HotbarEntry {
        int slot;
        String itemId;
        int count;
        boolean edible;
        int damage;
        int maxDamage;
        int remainingDurability;
    }

    private static final class InventoryEntry {
        int slot;
        int containerSlot;
        boolean inHotbar;
        String itemId;
        int count;
        boolean edible;
        int damage;
        int maxDamage;
        int remainingDurability;
    }
}
