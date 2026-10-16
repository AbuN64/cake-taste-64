#include "assets/as_public.h"
#include "common/com_public.h"
#include "arena_controller.h"
#include "input/in_public.h"
#include "input/in_replay.h"
#include "platform/sys_public.h"
#include "renderer/r_public.h"
#include "sound/snd_public.h"
#include <errno.h>
#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

enum { REPLAY_BUFFER_BYTES = 256 * 1024, GAME_TICK_RATE = 60 };
typedef struct {
    bool headless, fullscreen;
    uint64_t frames;
    const char *data_directory, *map, *command, *replay, *capture, *state;
} Options;
static bool parse_options(int argc, char **argv, Options *options) {
    *options = (Options){.data_directory = "data", .map = "arena0.rwa"};
    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--headless") == 0)
            options->headless = true;
        else if (strcmp(argv[i], "--fullscreen") == 0)
            options->fullscreen = true;
        else if (strcmp(argv[i], "--data-dir") == 0 && i + 1 < argc)
            options->data_directory = argv[++i];
        else if (strcmp(argv[i], "--map") == 0 && i + 1 < argc)
            options->map = argv[++i];
        else if (strcmp(argv[i], "--exec") == 0 && i + 1 < argc)
            options->command = argv[++i];
        else if (strcmp(argv[i], "--replay") == 0 && i + 1 < argc)
            options->replay = argv[++i];
        else if (strcmp(argv[i], "--capture") == 0 && i + 1 < argc)
            options->capture = argv[++i];
        else if (strcmp(argv[i], "--state") == 0 && i + 1 < argc)
            options->state = argv[++i];
        else if (strcmp(argv[i], "--frames") == 0 && i + 1 < argc) {
            const char *argument = argv[++i];
            char *end;
            errno = 0;
            options->frames = strtoull(argument, &end, 10);
            if (errno || *argument < '0' || *argument > '9' || *end || !options->frames ||
                options->frames > 10000000)
                return false;
        } else
            return false;
    }
    if ((options->headless || options->replay) && !options->frames)
        options->frames = 600;
    return !(options->headless && options->capture);
}
static void mix_audio(void *context, float *stereo, size_t frames) {
    S_Mix(context, stereo, frames);
}
static In_Replay *load_replay(const char *path) {
    if (!path)
        return NULL;
    In_Replay *replay = Sys_Alloc(sizeof(*replay));
    char *text = Sys_Alloc(REPLAY_BUFFER_BYTES);
    size_t size = 0;
    if (!replay || !text || !Sys_ReadFile(path, text, REPLAY_BUFFER_BYTES - 1, &size))
        Sys_Error("cannot read input replay: %s", path);
    text[size] = '\0';
    if (!In_ReplayParse(replay, text))
        Sys_Error("invalid input replay: %s", path);
    Sys_Free(text);
    return replay;
}
static void load_arena(const Com_Filesystem *files, const char *map, AS_Arena *arena) {
    void *bytes = Sys_Alloc(AS_MAX_FILE_BYTES);
    size_t size = 0;
    if (!bytes || !Com_FSRead(files, map, bytes, AS_MAX_FILE_BYTES, &size))
        Sys_Error("cannot read native arena: %s", map);
    char error[256] = {0};
    if (!AS_DecodeArena(bytes, size, arena, error, sizeof(error)))
        Sys_Error("invalid native arena %s: %s", map, error);
    Sys_Free(bytes);
}
/* The only transient geometry is a native crosshair and a brief hit marker. */
static void build_feedback(const G_Game *game, R_Vertex *vertices, uint32_t *indices,
                           size_t first_vertex, size_t first_index, R_Mesh *mesh,
                           R_Batch *feedback_batch) {
    float cy = cosf(game->yaw), sy = sinf(game->yaw);
    float cp = cosf(game->pitch), sp = sinf(game->pitch);
    const float forward[3] = {sy * cp, sp, -cy * cp};
    const float right[3] = {cy, 0, sy};
    const float up[3] = {-sy * sp, cp, cy * sp};
    float distance = game->config.near_plane * 3;
    float center[3];
    for (size_t a = 0; a < 3; ++a)
        center[a] = game->position[a] + forward[a] * distance;
    center[1] += game->config.eye_height;
    const float corners[8][2] = {{-0.013f, -0.0015f}, {0.013f, -0.0015f},  {0.013f, 0.0015f},
                                 {-0.013f, 0.0015f},  {-0.0015f, -0.013f}, {0.0015f, -0.013f},
                                 {0.0015f, 0.013f},   {-0.0015f, 0.013f}};
    for (size_t v = 0; v < 8; ++v) {
        R_Vertex *vertex = &vertices[first_vertex + v];
        for (size_t a = 0; a < 3; ++a)
            vertex->position[a] =
                center[a] + distance * (right[a] * corners[v][0] + up[a] * corners[v][1]);
        vertex->color[0] = 1;
        vertex->color[1] = game->shot_age < 0.12f ? 0.3f : 1;
        vertex->color[2] = game->shot_age < 0.12f ? 0.05f : 1;
        vertex->color[3] = 1;
        vertex->uv[0] = vertex->uv[1] = 0;
    }
    const uint32_t cross_indices[12] = {0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7};
    for (size_t i = 0; i < 12; ++i)
        indices[first_index + i] = (uint32_t)first_vertex + cross_indices[i];
    mesh->vertex_count = first_vertex + 8;
    mesh->index_count = first_index + 12;
    feedback_batch->index_count = 12;
    if (game->last_shot_hit && game->shot_age < 0.25f) {
        float radius = game->config.radius * 0.25f;
        const float offsets[6][3] = {{1, 0, 0},  {-1, 0, 0}, {0, 1, 0},
                                     {0, -1, 0}, {0, 0, 1},  {0, 0, -1}};
        for (size_t v = 0; v < 6; ++v) {
            R_Vertex *vertex = &vertices[first_vertex + 8 + v];
            for (size_t a = 0; a < 3; ++a)
                vertex->position[a] = game->last_shot_point[a] + offsets[v][a] * radius;
            vertex->color[0] = vertex->color[3] = 1;
            vertex->color[1] = 0.5f;
            vertex->color[2] = 0.05f;
            vertex->uv[0] = vertex->uv[1] = 0;
        }
        const uint32_t impact[24] = {0, 2, 4, 4, 2, 1, 1, 2, 5, 5, 2, 0,
                                     0, 4, 3, 4, 1, 3, 1, 5, 3, 5, 0, 3};
        for (size_t i = 0; i < 24; ++i)
            indices[first_index + 12 + i] = (uint32_t)first_vertex + 8 + impact[i];
        mesh->vertex_count += 6;
        mesh->index_count += 24;
        feedback_batch->index_count += 24;
    }
}
static void save_state(const char *path, const AS_Arena *arena, const G_Game *game) {
    char output[2048];
    int length =
        snprintf(output, sizeof(output),
                 "{\n  \"arena\": %u, \"ticks\": %" PRIu64
                 ",\n  \"position\": [%.9g, %.9g, %.9g], \"yaw\": %.9g, \"pitch\": %.9g,"
                 "\n  \"grounded\": %s, \"vertical_velocity\": %.9g,"
                 "\n  \"jumps\": %" PRIu64 ", \"landings\": %" PRIu64 ", \"collisions\": %" PRIu64
                 ", \"shots\": %" PRIu64 ", \"hits\": %" PRIu64 ",\n  \"last_shot_hit\": %s,"
                 " \"last_shot_point\": [%.9g, %.9g, %.9g],"
                 "\n  \"vertices\": %zu, \"indices\": %zu\n}\n",
                 arena->arena_id, game->ticks, (double)game->position[0], (double)game->position[1],
                 (double)game->position[2], (double)game->yaw, (double)game->pitch,
                 game->grounded ? "true" : "false", (double)game->vertical_velocity, game->jumps,
                 game->landings, game->collisions, game->shots, game->hits,
                 game->last_shot_hit ? "true" : "false", (double)game->last_shot_point[0],
                 (double)game->last_shot_point[1], (double)game->last_shot_point[2],
                 arena->mesh.vertex_count, arena->mesh.index_count);
    if (length < 0 || (size_t)length >= sizeof(output) ||
        !Sys_WriteFile(path, output, (size_t)length))
        Sys_Error("cannot write game state: %s", path);
}
int main(int argc, char **argv) {
    Options options;
    if (!parse_options(argc, argv, &options)) {
        fprintf(stderr, "usage: cake-taste-rage-scene-probe [--headless] [--frames N] [--fullscreen] "
                        "[--data-dir PATH] [--map FILE] [--replay FILE] "
                        "[--capture FILE.ppm] [--state FILE.json] [--exec COMMAND]\n");
        return EXIT_FAILURE;
    }
    const Sys_Config platform = {.application_id = "ragewars-experiment",
                                 .window_title = "EXPERIMENT | Native arena preview",
                                 .width = 1280,
                                 .height = 720,
                                 .headless = options.headless};
    if (!Sys_Init(&platform))
        return EXIT_FAILURE;
    uint64_t start_time = Sys_Nanoseconds();
    Com_Context *common = Sys_Alloc(sizeof(*common));
    char tick_rate_text[16];
    snprintf(tick_rate_text, sizeof(tick_rate_text), "%u", GAME_TICK_RATE);
    if (!common || !Com_Init(common, options.data_directory) ||
        !Com_CvarRegister(&common->cvars, "tick_rate", tick_rate_text, COM_CVAR_READONLY))
        Sys_Error("common initialization failed");
    AS_Arena arena = {0};
    load_arena(&common->files, options.map, &arena);
    if (arena.mesh.vertex_count > R_MAX_VERTICES - 14 ||
        arena.mesh.index_count > R_MAX_INDICES - 36 ||
        arena.mesh.material_count >= R_MAX_MATERIALS || arena.mesh.batch_count >= R_MAX_BATCHES)
        Sys_Error("arena exceeds renderer capacity");
    G_Game game;
    G_Config config = G_DefaultConfig(arena.mesh, arena.spawn, 1.0f);
    if (!G_Init(&game, &config))
        Sys_Error("game initialization failed");
    R_Vertex *vertices = Sys_Alloc((arena.mesh.vertex_count + 14) * sizeof(*vertices));
    uint32_t *indices = Sys_Alloc((arena.mesh.index_count + 36) * sizeof(*indices));
    R_Material *materials = Sys_Alloc((arena.mesh.material_count + 1) * sizeof(*materials));
    R_Batch *batches = Sys_Alloc((arena.mesh.batch_count + 1) * sizeof(*batches));
    if (!vertices || !indices || !materials || !batches)
        Sys_Error("render geometry allocation failed");
    memcpy(vertices, arena.mesh.vertices, arena.mesh.vertex_count * sizeof(*vertices));
    memcpy(indices, arena.mesh.indices, arena.mesh.index_count * sizeof(*indices));
    memcpy(materials, arena.mesh.materials, arena.mesh.material_count * sizeof(*materials));
    memcpy(batches, arena.mesh.batches, arena.mesh.batch_count * sizeof(*batches));
    materials[arena.mesh.material_count] = (R_Material){.texture_index = R_TEXTURE_NONE,
                                                        .wrap_u = R_WRAP_CLAMP,
                                                        .wrap_v = R_WRAP_CLAMP,
                                                        .filter = R_FILTER_NEAREST,
                                                        .alpha_mode = R_ALPHA_OPAQUE,
                                                        .alpha_cutoff = 0.5f};
    R_Batch *feedback_batch = &batches[arena.mesh.batch_count];
    *feedback_batch = (R_Batch){.first_index = (uint32_t)arena.mesh.index_count,
                                .index_count = 12,
                                .material_index = (uint32_t)arena.mesh.material_count};
    R_Mesh mesh = arena.mesh;
    mesh.vertices = vertices;
    mesh.indices = indices;
    mesh.materials = materials;
    mesh.material_count++;
    mesh.batches = batches;
    mesh.batch_count++;
    build_feedback(&game, vertices, indices, arena.mesh.vertex_count, arena.mesh.index_count, &mesh,
                   feedback_batch);
    if (!Sys_SetRenderResources(&mesh))
        Sys_Error("render resource initialization failed");
    In_Replay *replay = load_replay(options.replay);
    if (options.command && !Com_CommandAppend(&common->commands, options.command))
        Sys_Error("command is too long");
    Com_CommandExecute(&common->commands, 64);
    float volume = fmaxf(0, fminf(1, Com_CvarNumber(&common->cvars, "volume")));
    S_Mixer mixer;
    S_Init(&mixer);
    if (!Sys_AudioOpen(mix_audio, &mixer))
        Sys_Error("audio initialization failed");
    if (!Sys_SetFullscreen(options.fullscreen) || !Sys_SetMouseCapture(replay == NULL))
        Sys_Error("window input initialization failed");
    In_State input_state;
    In_Init(&input_state);
    Com_TickClock clock;
    if (!Com_TickInit(&clock, GAME_TICK_RATE))
        Sys_Error("tick initialization failed");
    uint64_t previous = Sys_Nanoseconds(), frames = 0, dropped = 0;
    bool fullscreen_down = false;
    double mix_energy = 0;
    char summary[512];
    snprintf(summary, sizeof(summary),
             "scene: %s arena=%u vertices=%zu triangles=%zu "
             "textures=%zu materials=%zu batches=%zu spawn=(%.3f,%.3f,%.3f)",
             arena.name, arena.arena_id, arena.mesh.vertex_count, arena.mesh.index_count / 3,
             arena.mesh.texture_count, arena.mesh.material_count, arena.mesh.batch_count,
             (double)arena.spawn[0], (double)arena.spawn[1], (double)arena.spawn[2]);
    Com_Print(common, summary);
    Com_Print(common, "WASD move; mouse look; Space jump; left mouse/Ctrl fire; Escape quits");
    while (common->running && (!options.frames || frames < options.frames)) {
        Sys_Input input;
        if (!Sys_PollInput(&input))
            Sys_Error("input polling failed");
        if (input.quit || input.keys[SYS_KEY_ESCAPE])
            break;
        if (!replay)
            In_Update(&input_state, &input);
        if (input.keys[SYS_KEY_F11] && !fullscreen_down) {
            options.fullscreen = !options.fullscreen;
            if (!Sys_SetFullscreen(options.fullscreen))
                Sys_Error("fullscreen switch failed");
        }
        fullscreen_down = input.keys[SYS_KEY_F11];
        char line[COM_COMMAND_TEXT];
        if (!options.headless && !replay && Sys_ConsolePoll(line, sizeof(line)))
            (void)Com_CommandAppend(&common->commands, line);
        Com_CommandExecute(&common->commands, 64);
        uint64_t now = Sys_Nanoseconds();
        Com_TickResult tick = Com_TickAdvance(
            &clock,
            options.headless || replay ? (1000000000ULL + GAME_TICK_RATE - 1) / GAME_TICK_RATE
                                       : now - previous,
            8);
        previous = now;
        dropped += tick.dropped_ns;
        for (uint32_t step = 0; step < tick.steps; ++step) {
            if (replay) {
                In_ReplayInput(replay, game.ticks, &input);
                In_Update(&input_state, &input);
            }
            const G_Input game_input = {.forward = (float)input_state.held[0][IN_FORWARD] -
                                                   (float)input_state.held[0][IN_BACK],
                                        .strafe = (float)input_state.held[0][IN_RIGHT] -
                                                  (float)input_state.held[0][IN_LEFT],
                                        .look_x = input_state.look_x,
                                        .look_y = input_state.look_y,
                                        .jump_pressed = input_state.pressed[0][IN_JUMP],
                                        .fire_pressed = input_state.pressed[0][IN_FIRE]};
            G_Tick(&game, &game_input, 1.0f / GAME_TICK_RATE);
            if (game_input.fire_pressed) {
                Sys_AudioLock();
                (void)S_StartTone(&mixer, game.last_shot_hit ? 880 : 440, volume, 0.1);
                Sys_AudioUnlock();
            }
            if (options.headless) {
                float samples[1600];
                S_Mix(&mixer, samples, 800);
                for (size_t i = 0; i < 1600; ++i)
                    mix_energy += fabs((double)samples[i]);
            }
            In_EndTick(&input_state);
        }
        build_feedback(&game, vertices, indices, arena.mesh.vertex_count, arena.mesh.index_count,
                       &mesh, feedback_batch);
        float matrix[16];
        R_CommandList render;
        const float clear[4] = {0.025f, 0.035f, 0.06f, 1};
        R_Begin(&render, clear);
        if (!G_ViewProjection(&game, Sys_VideoAspect(), matrix) ||
            !R_SetViewProjection(&render, matrix) || !R_SubmitMesh(&render, &mesh) ||
            !Sys_Render(&render))
            Sys_Error("render submission failed");
        ++frames;
        if (!options.headless && !replay)
            Sys_Sleep(1);
    }
    if (options.capture && !Sys_CaptureFrame(options.capture))
        Sys_Error("frame capture failed: %s", options.capture);
    if (options.state)
        save_state(options.state, &arena, &game);
    Sys_AudioClose();
    snprintf(summary, sizeof(summary),
             "shutdown: frames=%" PRIu64 " ticks=%" PRIu64 " dropped_ns=%" PRIu64
             " mix_energy=%.3f position=(%.6f,%.6f,%.6f) shots=%" PRIu64 " hits=%" PRIu64
             " jumps=%" PRIu64 " landings=%" PRIu64 " collisions=%" PRIu64 " elapsed_ms=%.3f",
             frames, clock.ticks, dropped, mix_energy, (double)game.position[0],
             (double)game.position[1], (double)game.position[2], game.shots, game.hits, game.jumps,
             game.landings, game.collisions, (double)(Sys_Nanoseconds() - start_time) / 1000000.0);
    Com_Print(common, summary);
    Sys_Free(replay);
    Sys_Free(batches);
    Sys_Free(materials);
    Sys_Free(indices);
    Sys_Free(vertices);
    AS_FreeArena(&arena);
    Com_Shutdown(common);
    Sys_Free(common);
    Sys_Shutdown();
    return EXIT_SUCCESS;
}
