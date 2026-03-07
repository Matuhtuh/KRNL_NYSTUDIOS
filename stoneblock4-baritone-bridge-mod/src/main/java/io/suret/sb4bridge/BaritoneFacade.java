package io.suret.sb4bridge;

import java.lang.reflect.Method;

final class BaritoneFacade {
    private volatile ReflectionHandles handles;

    StatusSnapshot snapshot() {
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ReflectionHandles h = resolve();
                Object baritone = h.fetchPrimaryBaritone();
                Object pathing = h.getPathingBehavior == null ? null : h.getPathingBehavior.invoke(baritone);
                boolean isPathing = false;
                if (pathing != null && h.isPathing != null) {
                    Object raw = h.isPathing.invoke(pathing);
                    if (raw instanceof Boolean b) {
                        isPathing = b;
                    }
                }
                Object goal = (pathing != null && h.getGoal != null) ? h.getGoal.invoke(pathing) : null;
                String goalText = goal == null ? "" : goal.toString();
                return new StatusSnapshot(true, isPathing, goalText, "");
            } catch (Throwable ex) {
                handles = null;
                if (attempt == 0 && isLikelyHandleMismatch(ex)) {
                    continue;
                }
                return new StatusSnapshot(false, false, "", simplify(ex));
            }
        }
        return new StatusSnapshot(false, false, "", "unreachable");
    }

    ExecutionResult execute(String command) {
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ReflectionHandles h = resolve();
                Object baritone = h.fetchPrimaryBaritone();
                Object commandManager = h.getCommandManager.invoke(baritone);
                Object rawResult = h.execute.invoke(commandManager, command);
                boolean accepted = coerceExecuteResult(rawResult);
                return new ExecutionResult(accepted ? "accepted" : "rejected", accepted, "");
            } catch (Throwable ex) {
                handles = null;
                if (attempt == 0 && isLikelyHandleMismatch(ex)) {
                    continue;
                }
                return new ExecutionResult("error", false, simplify(ex));
            }
        }
        return new ExecutionResult("error", false, "unreachable");
    }

    private ReflectionHandles resolve() throws Exception {
        ReflectionHandles current = handles;
        if (current != null) {
            return current;
        }
        synchronized (this) {
            if (handles != null) {
                return handles;
            }
            try {
                Class<?> apiClass = Class.forName("baritone.api.BaritoneAPI");
                Method getProvider = apiClass.getMethod("getProvider");
                Object providerObj = getProvider.invoke(null);
                Class<?> providerClass = providerObj.getClass();

                Method getPrimaryBaritone = providerClass.getMethod("getPrimaryBaritone");
                Object baritone = getPrimaryBaritone.invoke(providerObj);
                Class<?> baritoneClass = baritone.getClass();

                Method getCommandManager = baritoneClass.getMethod("getCommandManager");
                Object commandManager = getCommandManager.invoke(baritone);
                Method execute = findExecuteMethod(commandManager.getClass());
                if (execute == null) {
                    throw new NoSuchMethodException("Baritone command manager execute(String) not found");
                }

                Method getPathingBehavior = findMethod(baritoneClass, "getPathingBehavior");
                Method isPathing = null;
                Method getGoal = null;
                if (getPathingBehavior != null) {
                    Object pathingBehavior = getPathingBehavior.invoke(baritone);
                    if (pathingBehavior != null) {
                        Class<?> pathingClass = pathingBehavior.getClass();
                        isPathing = findMethod(pathingClass, "isPathing");
                        getGoal = findMethod(pathingClass, "getGoal");
                    }
                }

                handles = new ReflectionHandles(getProvider, getPrimaryBaritone, getCommandManager, execute, getPathingBehavior, isPathing, getGoal);
                return handles;
            } catch (Exception ex) {
                handles = null;
                throw ex;
            }
        }
    }

    private static String simplify(Throwable ex) {
        Throwable cause = ex;
        while (cause.getCause() != null) {
            cause = cause.getCause();
        }
        String msg = cause.getMessage();
        if (msg == null || msg.isBlank()) {
            msg = cause.getClass().getSimpleName();
        }
        return msg;
    }

    private static boolean isLikelyHandleMismatch(Throwable ex) {
        Throwable cause = ex;
        while (cause != null) {
            if (cause instanceof IllegalArgumentException) {
                return true;
            }
            cause = cause.getCause();
        }
        return false;
    }

    private static Method findMethod(Class<?> type, String name, Class<?>... parameterTypes) {
        try {
            return type.getMethod(name, parameterTypes);
        } catch (NoSuchMethodException ignored) {
            return null;
        }
    }

    private static Method findExecuteMethod(Class<?> type) {
        Method direct = findMethod(type, "execute", String.class);
        if (direct != null) {
            return direct;
        }
        for (Method method : type.getMethods()) {
            if (!method.getName().equals("execute")) {
                continue;
            }
            Class<?>[] params = method.getParameterTypes();
            if (params.length == 1 && params[0] == String.class) {
                return method;
            }
        }
        return null;
    }

    private static boolean coerceExecuteResult(Object rawResult) {
        if (rawResult == null) {
            return true;
        }
        if (rawResult instanceof Boolean b) {
            return b;
        }
        if (rawResult instanceof Number n) {
            return n.intValue() != 0;
        }
        String lowered = rawResult.toString().trim().toLowerCase();
        if (lowered.isBlank()) {
            return true;
        }
        if ("false".equals(lowered) || lowered.contains("reject") || lowered.contains("error")) {
            return false;
        }
        return true;
    }

    static final class StatusSnapshot {
        final boolean loaded;
        final boolean isPathing;
        final String goal;
        final String errorMessage;

        StatusSnapshot(boolean loaded, boolean isPathing, String goal, String errorMessage) {
            this.loaded = loaded;
            this.isPathing = isPathing;
            this.goal = goal;
            this.errorMessage = loaded ? "" : errorMessage;
        }
    }

    static final class ExecutionResult {
        final String status;
        final boolean accepted;
        final String errorMessage;

        ExecutionResult(String status, boolean accepted, String errorMessage) {
            this.status = status;
            this.accepted = accepted;
            this.errorMessage = errorMessage;
        }
    }

    private record ReflectionHandles(
            Method getProvider,
            Method getPrimaryBaritone,
            Method getCommandManager,
            Method execute,
            Method getPathingBehavior,
            Method isPathing,
            Method getGoal
    ) {
        Object fetchPrimaryBaritone() throws Exception {
            Object provider = getProvider.invoke(null);
            return getPrimaryBaritone.invoke(provider);
        }
    }
}
