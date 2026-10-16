#include "arena_controller.h"
#include "test_check.h"

#include <math.h>

static bool Near(float a, float b, float tolerance) {
    return fabsf(a - b) < tolerance;
}

int main(void) {
    const R_Vertex vertices[] = {
        {.position = {-5.0f, 0.0f, -5.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {5.0f, 0.0f, -5.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {5.0f, 0.0f, 5.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {-5.0f, 0.0f, 5.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {-5.0f, 0.0f, -2.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {5.0f, 0.0f, -2.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {5.0f, 3.0f, -2.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
        {.position = {-5.0f, 3.0f, -2.0f}, .color = {1, 1, 1, 1}, .uv = {0, 0}},
    };
    const uint32_t indices[] = {0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7};
    R_Mesh mesh = {.vertices = vertices, .vertex_count = 8, .indices = indices, .index_count = 12};
    float spawn[3] = {0.0f, 0.0f, 0.0f};
    G_Config config = G_DefaultConfig(mesh, spawn, 1.0f);
    G_Game game;
    Test_Check(G_Init(&game, &config), "initialize native game against triangle mesh");
    Test_Check(game.position[1] == 0.0f && game.shot_age == 999.0f, "initial state");

    G_Input input = {.fire_pressed = true};
    G_Tick(&game, &input, 1.0f / 60.0f);
    Test_Check(game.shots == 1 && game.hits == 1 && game.last_shot_hit,
               "two-sided hitscan hits wall");
    Test_Check(Near(game.last_shot_distance, 2.0f, 0.001f) &&
                   Near(game.last_shot_point[2], -2.0f, 0.001f),
               "impact feedback contains wall position");
    Test_Check(game.grounded && game.landings == 1, "floor collision establishes grounding");

    input = (G_Input){.jump_pressed = true};
    G_Tick(&game, &input, 1.0f / 60.0f);
    Test_Check(game.jumps == 1 && game.position[1] > 0.0f, "jump lifts player");
    for (int i = 0; i < 80; ++i)
        G_Tick(&game, &(G_Input){0}, 1.0f / 60.0f);
    Test_Check(game.grounded && game.landings >= 2 && Near(game.position[1], 0.0f, 0.001f),
               "gravity returns player to floor");

    for (int i = 0; i < 100; ++i)
        G_Tick(&game, &(G_Input){.forward = 1.0f}, 1.0f / 60.0f);
    Test_Check(game.position[2] < -1.0f && game.position[2] > -1.75f && game.collisions > 0,
               "bounded movement reaches wall but does not pass through it");

    float matrix[16];
    Test_Check(G_ViewProjection(&game, 16.0f / 9.0f, matrix), "build camera matrix");
    for (int i = 0; i < 16; ++i)
        Test_Check(isfinite(matrix[i]), "camera matrix is finite");
    Test_Check(!G_ViewProjection(&game, 0.0f, matrix), "reject invalid aspect ratio");

    config.mesh_scale = 0.0f;
    Test_Check(!G_Init(&game, &config), "reject invalid mesh scale");
    return 0;
}
