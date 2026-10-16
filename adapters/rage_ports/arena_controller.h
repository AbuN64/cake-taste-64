#ifndef CAKE_TASTE_RAGE_PORTS_CONTROLLER_H
#define CAKE_TASTE_RAGE_PORTS_CONTROLLER_H

#include "renderer/r_public.h"
#include <stdbool.h>
#include <stdint.h>

/* Mesh arrays stay owned by the caller and must outlive the game.
 * Mesh coordinates are multiplied by mesh_scale; spawn and all gameplay
 * dimensions are already in the resulting world units. Y is up. */
typedef struct {
    R_Mesh mesh;
    float mesh_scale;
    float spawn[3];
    float radius;
    float height;
    float eye_height;
    float move_speed;
    float jump_speed;
    float gravity;
    float look_sensitivity;
    float fov_y_radians;
    float near_plane;
    float far_plane;
} G_Config;

typedef struct {
    float forward; /* -1 back, +1 forward */
    float strafe;  /* -1 left, +1 right */
    float look_x;  /* positive turns right */
    float look_y;  /* positive turns down */
    bool jump_pressed;
    bool fire_pressed;
} G_Input;

typedef struct {
    G_Config config;
    float position[3]; /* feet in world coordinates */
    float yaw;
    float pitch;
    float vertical_velocity;
    bool grounded;
    uint64_t ticks;
    uint64_t jumps;
    uint64_t landings;
    uint64_t collisions;
    uint64_t shots;
    uint64_t hits;
    bool last_shot_hit;
    float last_shot_point[3];
    float last_shot_distance;
    float shot_age; /* seconds since last shot; 999 on initialization */
} G_Game;

G_Config G_DefaultConfig(R_Mesh mesh, const float spawn[3], float mesh_scale);
bool G_Init(G_Game *game, const G_Config *config);
void G_Tick(G_Game *game, const G_Input *input, float dt);
bool G_ViewProjection(const G_Game *game, float aspect, float out[16]);

#endif
