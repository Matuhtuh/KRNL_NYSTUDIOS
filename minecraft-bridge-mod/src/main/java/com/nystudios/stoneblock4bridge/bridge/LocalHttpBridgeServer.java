package com.nystudios.stoneblock4bridge.bridge;

import com.google.gson.FieldNamingPolicy;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonSyntaxException;
import com.nystudios.stoneblock4bridge.action.ActionExecutor;
import com.nystudios.stoneblock4bridge.api.BridgeProtocol;
import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import com.nystudios.stoneblock4bridge.dto.ErrorResponseDto;
import com.nystudios.stoneblock4bridge.dto.GameStateSnapshotDto;
import com.nystudios.stoneblock4bridge.dto.HeartbeatResponseDto;
import com.nystudios.stoneblock4bridge.state.StateProvider;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Localhost-only HTTP transport for first end-to-end local debugging.
 */
public final class LocalHttpBridgeServer implements BridgeServer {
    private final StateProvider stateProvider;
    private final ActionExecutor actionExecutor;
    private final Gson gson = new GsonBuilder()
            .setFieldNamingPolicy(FieldNamingPolicy.LOWER_CASE_WITH_UNDERSCORES)
            .create();
    private final AtomicLong tickCounter = new AtomicLong();
    private HttpServer server;

    public LocalHttpBridgeServer(StateProvider stateProvider, ActionExecutor actionExecutor) {
        this.stateProvider = stateProvider;
        this.actionExecutor = actionExecutor;
    }

    @Override
    public void start() {
        try {
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 8765), 0);
            this.server.createContext("/heartbeat", new HeartbeatHandler());
            this.server.createContext("/state", new StateHandler());
            this.server.createContext("/inventory", new InventoryHandler());
            this.server.createContext("/screen", new ScreenHandler());
            this.server.createContext("/action", new ActionHandler());
            this.server.start();
        } catch (IOException exception) {
            throw new IllegalStateException("Failed to start local bridge server", exception);
        }
    }

    @Override
    public void stop() {
        if (this.server != null) {
            this.server.stop(0);
            this.server = null;
        }
    }

    private final class HeartbeatHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            writeJson(exchange, 200, new HeartbeatResponseDto("ok", BridgeProtocol.VERSION, "client_local_http"));
        }
    }

    private final class StateHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            GameStateSnapshotDto snapshot = stateProvider.getFullStateSnapshot(tickCounter.incrementAndGet());
            writeJson(exchange, 200, snapshot);
        }
    }

    private final class InventoryHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            writeJson(exchange, 200, stateProvider.getInventoryContents());
        }
    }

    private final class ScreenHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            writeJson(exchange, 200, stateProvider.getFullStateSnapshot(tickCounter.get()).openScreen());
        }
    }

    private final class ActionHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String body = readBody(exchange.getRequestBody());
            ActionRequestDto request;
            try {
                request = gson.fromJson(body, ActionRequestDto.class);
            } catch (JsonSyntaxException exception) {
                writeJson(exchange, 400, new ErrorResponseDto("bad_json", "Unable to parse action payload"));
                return;
            }

            if (request == null || request.actionType() == null || request.requestId() == null) {
                writeJson(exchange, 400, new ErrorResponseDto("bad_request", "request_id and action_type are required"));
                return;
            }

            ActionResultDto result = actionExecutor.performAction(request);
            int status = result.accepted() ? 200 : 400;
            writeJson(exchange, status, result);
        }

        private String readBody(InputStream stream) throws IOException {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private void writeJson(HttpExchange exchange, int status, Object payload) throws IOException {
        byte[] bytes = gson.toJson(payload).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }
}
