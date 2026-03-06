package com.nystudios.stoneblock4bridge.bridge;

import com.nystudios.stoneblock4bridge.action.ActionExecutor;
import com.nystudios.stoneblock4bridge.dto.ActionRequestDto;
import com.nystudios.stoneblock4bridge.dto.ActionResultDto;
import com.nystudios.stoneblock4bridge.dto.BridgeStateSnapshotDto;
import com.nystudios.stoneblock4bridge.screen.ScreenInspector;
import com.nystudios.stoneblock4bridge.state.StateProvider;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

/**
 * Localhost-only HTTP transport.
 *
 * <p>HTTP is chosen as the simplest reliable bridge for early development:
 * easy to inspect, language-agnostic for the external AI process, and stable on localhost.
 * This class intentionally returns conservative responses until JSON serialization and full
 * request parsing are implemented.</p>
 */
public final class LocalHttpBridgeServer implements BridgeServer {
    private final StateProvider stateProvider;
    private final ScreenInspector screenInspector;
    private final ActionExecutor actionExecutor;
    private HttpServer server;

    public LocalHttpBridgeServer(StateProvider stateProvider, ScreenInspector screenInspector, ActionExecutor actionExecutor) {
        this.stateProvider = stateProvider;
        this.screenInspector = screenInspector;
        this.actionExecutor = actionExecutor;
    }

    @Override
    public void start() {
        try {
            this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 8765), 0);
            this.server.createContext("/health", new HealthHandler());
            this.server.createContext("/state", new StateHandler(stateProvider, screenInspector));
            this.server.createContext("/action", new ActionHandler(actionExecutor));
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

    private static final class HealthHandler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            write(exchange, 200, "ok");
        }
    }

    private static final class StateHandler implements HttpHandler {
        private final StateProvider stateProvider;
        private final ScreenInspector screenInspector;

        private StateHandler(StateProvider stateProvider, ScreenInspector screenInspector) {
            this.stateProvider = stateProvider;
            this.screenInspector = screenInspector;
        }

        @Override
        public void handle(HttpExchange exchange) throws IOException {
            BridgeStateSnapshotDto snapshot = stateProvider.getFullStateSnapshot();
            String body = "state endpoint placeholder; wire JSON serializer next. screen="
                    + screenInspector.inspectCurrentScreen().title()
                    + " player_dimension=" + snapshot.player().dimension();
            write(exchange, 200, body);
        }
    }

    private static final class ActionHandler implements HttpHandler {
        private final ActionExecutor actionExecutor;

        private ActionHandler(ActionExecutor actionExecutor) {
            this.actionExecutor = actionExecutor;
        }

        @Override
        public void handle(HttpExchange exchange) throws IOException {
            String body = readBody(exchange.getRequestBody());
            ActionRequestDto request = new ActionRequestDto("unparsed", "unknown", java.util.Map.of("raw", body), 20);
            ActionResultDto result = actionExecutor.performAction(request);
            write(exchange, 200, result.message());
        }

        private String readBody(InputStream stream) throws IOException {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private static void write(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }
}
