#include "arena_controller.h"

#include <math.h>
#include <string.h>

typedef struct {
    float x, y, z;
} G_Vec3;

static G_Vec3 G_Vec(float x, float y, float z) {
    return (G_Vec3){x, y, z};
}
static G_Vec3 G_Add(G_Vec3 a, G_Vec3 b) {
    return G_Vec(a.x + b.x, a.y + b.y, a.z + b.z);
}
static G_Vec3 G_Sub(G_Vec3 a, G_Vec3 b) {
    return G_Vec(a.x - b.x, a.y - b.y, a.z - b.z);
}
static G_Vec3 G_Mul(G_Vec3 a, float n) {
    return G_Vec(a.x * n, a.y * n, a.z * n);
}
static float G_Dot(G_Vec3 a, G_Vec3 b) {
    return a.x * b.x + a.y * b.y + a.z * b.z;
}
static G_Vec3 G_Cross(G_Vec3 a, G_Vec3 b) {
    return G_Vec(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
static float G_Length(G_Vec3 a) {
    return sqrtf(G_Dot(a, a));
}
static G_Vec3 G_Load(const float p[3]) {
    return G_Vec(p[0], p[1], p[2]);
}
static void G_Store(float p[3], G_Vec3 v) {
    p[0] = v.x;
    p[1] = v.y;
    p[2] = v.z;
}
static bool G_FiniteVec(G_Vec3 v) {
    return isfinite(v.x) && isfinite(v.y) && isfinite(v.z);
}
static float G_Clamp(float x, float lo, float hi) {
    return fminf(hi, fmaxf(lo, x));
}

static G_Vec3 G_Vertex(const G_Game *game, uint32_t index) {
    return G_Mul(G_Load(game->config.mesh.vertices[index].position), game->config.mesh_scale);
}

static G_Vec3 G_ClosestPoint(G_Vec3 p, G_Vec3 a, G_Vec3 b, G_Vec3 c) {
    G_Vec3 ab = G_Sub(b, a), ac = G_Sub(c, a), ap = G_Sub(p, a);
    float d1 = G_Dot(ab, ap), d2 = G_Dot(ac, ap);
    if (d1 <= 0.0f && d2 <= 0.0f)
        return a;
    G_Vec3 bp = G_Sub(p, b);
    float d3 = G_Dot(ab, bp), d4 = G_Dot(ac, bp);
    if (d3 >= 0.0f && d4 <= d3)
        return b;
    float vc = d1 * d4 - d3 * d2;
    if (vc <= 0.0f && d1 >= 0.0f && d3 <= 0.0f)
        return G_Add(a, G_Mul(ab, d1 / (d1 - d3)));
    G_Vec3 cp = G_Sub(p, c);
    float d5 = G_Dot(ab, cp), d6 = G_Dot(ac, cp);
    if (d6 >= 0.0f && d5 <= d6)
        return c;
    float vb = d5 * d2 - d1 * d6;
    if (vb <= 0.0f && d2 >= 0.0f && d6 <= 0.0f)
        return G_Add(a, G_Mul(ac, d2 / (d2 - d6)));
    float va = d3 * d6 - d5 * d4;
    if (va <= 0.0f && d4 - d3 >= 0.0f && d5 - d6 >= 0.0f)
        return G_Add(b, G_Mul(G_Sub(c, b), (d4 - d3) / ((d4 - d3) + (d5 - d6))));
    float denom = 1.0f / (va + vb + vc);
    return G_Add(a, G_Add(G_Mul(ab, vb * denom), G_Mul(ac, vc * denom)));
}

static bool G_NearTriangle(G_Vec3 center, float radius, G_Vec3 a, G_Vec3 b, G_Vec3 c) {
    return center.x >= fminf(a.x, fminf(b.x, c.x)) - radius &&
           center.x <= fmaxf(a.x, fmaxf(b.x, c.x)) + radius &&
           center.y >= fminf(a.y, fminf(b.y, c.y)) - radius &&
           center.y <= fmaxf(a.y, fmaxf(b.y, c.y)) + radius &&
           center.z >= fminf(a.z, fminf(b.z, c.z)) - radius &&
           center.z <= fmaxf(a.z, fmaxf(b.z, c.z)) + radius;
}

static bool G_Resolve(G_Game *game, G_Vec3 step) {
    const R_Mesh *mesh = &game->config.mesh;
    const float radius = game->config.radius;
    const float offsets[3] = {radius, game->config.height * 0.5f, game->config.height - radius};
    bool floor_contact = false;
    for (int pass = 0; pass < 4; ++pass) {
        bool changed = false;
        for (size_t tri = 0; tri < mesh->index_count; tri += 3) {
            G_Vec3 a = G_Vertex(game, mesh->indices[tri]);
            G_Vec3 b = G_Vertex(game, mesh->indices[tri + 1]);
            G_Vec3 c = G_Vertex(game, mesh->indices[tri + 2]);
            G_Vec3 face = G_Cross(G_Sub(b, a), G_Sub(c, a));
            float face_length = G_Length(face);
            if (face_length < 0.000001f)
                continue;
            for (int sample = 0; sample < 3; ++sample) {
                G_Vec3 center = G_Load(game->position);
                center.y += offsets[sample];
                if (!G_NearTriangle(center, radius, a, b, c))
                    continue;
                G_Vec3 closest = G_ClosestPoint(center, a, b, c);
                G_Vec3 delta = G_Sub(center, closest);
                float distance = G_Length(delta);
                if (distance >= radius)
                    continue;
                G_Vec3 normal;
                if (distance > 0.000001f) {
                    normal = G_Mul(delta, 1.0f / distance);
                } else {
                    normal = G_Mul(face, 1.0f / face_length);
                    if (G_Dot(normal, step) > 0.0f)
                        normal = G_Mul(normal, -1.0f);
                    if (G_Length(step) < 0.000001f && normal.y < 0.0f)
                        normal = G_Mul(normal, -1.0f);
                }
                G_Store(game->position,
                        G_Add(G_Load(game->position), G_Mul(normal, radius - distance + 0.00001f)));
                if (normal.y > 0.55f) {
                    floor_contact = true;
                    if (game->vertical_velocity < 0.0f)
                        game->vertical_velocity = 0.0f;
                } else if (normal.y < -0.55f && game->vertical_velocity > 0.0f) {
                    game->vertical_velocity = 0.0f;
                }
                ++game->collisions;
                changed = true;
            }
        }
        if (!changed)
            break;
    }
    return floor_contact;
}

static G_Vec3 G_Forward(const G_Game *game) {
    float cp = cosf(game->pitch);
    return G_Vec(sinf(game->yaw) * cp, sinf(game->pitch), -cosf(game->yaw) * cp);
}

static bool G_RayTriangle(G_Vec3 origin, G_Vec3 ray, G_Vec3 a, G_Vec3 b, G_Vec3 c,
                          float *distance) {
    G_Vec3 e1 = G_Sub(b, a), e2 = G_Sub(c, a);
    G_Vec3 p = G_Cross(ray, e2);
    float det = G_Dot(e1, p);
    if (fabsf(det) < 0.000001f)
        return false;
    float inv = 1.0f / det;
    G_Vec3 t = G_Sub(origin, a);
    float u = G_Dot(t, p) * inv;
    if (u < 0.0f || u > 1.0f)
        return false;
    G_Vec3 q = G_Cross(t, e1);
    float v = G_Dot(ray, q) * inv;
    if (v < 0.0f || u + v > 1.0f)
        return false;
    float hit = G_Dot(e2, q) * inv;
    if (hit <= 0.0001f)
        return false;
    *distance = hit;
    return true;
}

static void G_Fire(G_Game *game) {
    const R_Mesh *mesh = &game->config.mesh;
    G_Vec3 origin = G_Load(game->position);
    origin.y += game->config.eye_height;
    G_Vec3 ray = G_Forward(game);
    float nearest = game->config.far_plane;
    bool hit = false;
    for (size_t tri = 0; tri < mesh->index_count; tri += 3) {
        float distance;
        if (G_RayTriangle(origin, ray, G_Vertex(game, mesh->indices[tri]),
                          G_Vertex(game, mesh->indices[tri + 1]),
                          G_Vertex(game, mesh->indices[tri + 2]), &distance) &&
            distance < nearest) {
            nearest = distance;
            hit = true;
        }
    }
    ++game->shots;
    if (hit)
        ++game->hits;
    game->last_shot_hit = hit;
    game->last_shot_distance = nearest;
    game->shot_age = 0.0f;
    G_Store(game->last_shot_point, G_Add(origin, G_Mul(ray, nearest)));
}

G_Config G_DefaultConfig(R_Mesh mesh, const float spawn[3], float mesh_scale) {
    G_Config config = {
        .mesh = mesh,
        .mesh_scale = mesh_scale,
        .radius = 0.3f,
        .height = 1.7f,
        .eye_height = 1.55f,
        .move_speed = 4.5f,
        .jump_speed = 5.0f,
        .gravity = 16.0f,
        .look_sensitivity = 0.0005f,
        .fov_y_radians = 1.25f,
        .near_plane = 0.05f,
        .far_plane = 500.0f,
    };
    if (spawn)
        memcpy(config.spawn, spawn, sizeof(config.spawn));
    return config;
}

bool G_Init(G_Game *game, const G_Config *config) {
    if (!game || !config || !config->mesh.vertices || !config->mesh.indices ||
        config->mesh.vertex_count == 0 || config->mesh.index_count == 0 ||
        config->mesh.index_count % 3 != 0 || !isfinite(config->mesh_scale) ||
        config->mesh_scale <= 0.0f || !G_FiniteVec(G_Load(config->spawn)) ||
        !isfinite(config->radius) || config->radius <= 0.0f || !isfinite(config->height) ||
        config->height < 2.0f * config->radius || !isfinite(config->eye_height) ||
        config->eye_height < config->radius || config->eye_height > config->height ||
        !isfinite(config->move_speed) || config->move_speed <= 0.0f ||
        !isfinite(config->jump_speed) || config->jump_speed <= 0.0f || !isfinite(config->gravity) ||
        config->gravity <= 0.0f || !isfinite(config->look_sensitivity) ||
        config->look_sensitivity <= 0.0f || !isfinite(config->fov_y_radians) ||
        config->fov_y_radians <= 0.1f || config->fov_y_radians >= 3.0f ||
        !isfinite(config->near_plane) || config->near_plane <= 0.0f ||
        !isfinite(config->far_plane) || config->far_plane <= config->near_plane)
        return false;
    for (size_t i = 0; i < config->mesh.vertex_count; ++i) {
        G_Vec3 scaled = G_Mul(G_Load(config->mesh.vertices[i].position), config->mesh_scale);
        if (!G_FiniteVec(scaled))
            return false;
    }
    for (size_t i = 0; i < config->mesh.index_count; ++i)
        if (config->mesh.indices[i] >= config->mesh.vertex_count)
            return false;
    memset(game, 0, sizeof(*game));
    game->config = *config;
    memcpy(game->position, config->spawn, sizeof(game->position));
    game->shot_age = 999.0f;
    return true;
}

void G_Tick(G_Game *game, const G_Input *input, float dt) {
    if (!game || !isfinite(dt) || dt <= 0.0f)
        return;
    G_Input empty = {0};
    if (!input)
        input = &empty;
    dt = fminf(dt, 0.05f);
    ++game->ticks;
    game->shot_age = fminf(999.0f, game->shot_age + dt);
    if (isfinite(input->look_x))
        game->yaw += input->look_x * game->config.look_sensitivity;
    if (isfinite(input->look_y))
        game->pitch =
            G_Clamp(game->pitch - input->look_y * game->config.look_sensitivity, -1.48f, 1.48f);
    if (game->yaw > 3.14159265f || game->yaw < -3.14159265f)
        game->yaw = remainderf(game->yaw, 6.28318531f);
    if (input->fire_pressed)
        G_Fire(game);
    if (input->jump_pressed && game->grounded) {
        game->vertical_velocity = game->config.jump_speed;
        game->grounded = false;
        ++game->jumps;
    }
    float forward = isfinite(input->forward) ? G_Clamp(input->forward, -1.0f, 1.0f) : 0.0f;
    float strafe = isfinite(input->strafe) ? G_Clamp(input->strafe, -1.0f, 1.0f) : 0.0f;
    float length = sqrtf(forward * forward + strafe * strafe);
    if (length > 1.0f) {
        forward /= length;
        strafe /= length;
    }
    float sy = sinf(game->yaw), cy = cosf(game->yaw);
    G_Vec3 move = G_Vec((sy * forward + cy * strafe) * game->config.move_speed * dt, 0.0f,
                        (-cy * forward + sy * strafe) * game->config.move_speed * dt);
    game->vertical_velocity -= game->config.gravity * dt;
    move.y = game->vertical_velocity * dt;
    /* Each step travels no farther than a quarter of the body radius. The
     * cap also bounds work when a caller supplies an extreme speed. */
    float max_distance = game->config.radius * 32.0f;
    float travel = G_Length(move);
    if (travel > max_distance)
        move = G_Mul(move, max_distance / travel);
    int steps = (int)ceilf(G_Length(move) / (game->config.radius * 0.25f));
    if (steps < 1)
        steps = 1;
    if (steps > 128)
        steps = 128;
    G_Vec3 step = G_Mul(move, 1.0f / (float)steps);
    bool was_grounded = game->grounded;
    game->grounded = false;
    for (int i = 0; i < steps; ++i) {
        G_Store(game->position, G_Add(G_Load(game->position), step));
        if (G_Resolve(game, step))
            game->grounded = true;
    }
    if (!was_grounded && game->grounded)
        ++game->landings;
}

bool G_ViewProjection(const G_Game *game, float aspect, float out[16]) {
    if (!game || !out || !isfinite(aspect) || aspect <= 0.0f)
        return false;
    G_Vec3 eye = G_Load(game->position);
    eye.y += game->config.eye_height;
    G_Vec3 forward = G_Forward(game);
    G_Vec3 right = G_Vec(cosf(game->yaw), 0.0f, sinf(game->yaw));
    G_Vec3 up = G_Cross(right, forward);
    float view[16] = {
        right.x,
        up.x,
        -forward.x,
        0.0f,
        right.y,
        up.y,
        -forward.y,
        0.0f,
        right.z,
        up.z,
        -forward.z,
        0.0f,
        -G_Dot(right, eye),
        -G_Dot(up, eye),
        G_Dot(forward, eye),
        1.0f,
    };
    float f = 1.0f / tanf(game->config.fov_y_radians * 0.5f);
    float near = game->config.near_plane, far = game->config.far_plane;
    float projection[16] = {
        f / aspect,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        f,
        0.0f,
        0.0f,
        0.0f,
        0.0f,
        far / (near - far),
        -1.0f,
        0.0f,
        0.0f,
        far * near / (near - far),
        0.0f,
    };
    for (int col = 0; col < 4; ++col)
        for (int row = 0; row < 4; ++row) {
            out[col * 4 + row] = 0.0f;
            for (int k = 0; k < 4; ++k)
                out[col * 4 + row] += projection[k * 4 + row] * view[col * 4 + k];
        }
    return true;
}
