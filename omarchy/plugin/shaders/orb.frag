#version 440
// The Jarvis orb: a dark core with two eyes inside a glass shell of light. Original work for Jarvis
// WorkOS (MIT). Every input is a uniform driven by real jarvisd state in JarvisOrb.qml; this shader
// is a pure function of them and of `time`.
//
// The model is a small 3D scene, drawn analytically per pixel:
//   shell    the silhouette of a soft sphere: one crisp neon rim whose outline folds and dents (the
//            travelling folds of the reference), a faint glass fill that brightens toward the rim
//            (Fresnel) and a wide bloom. A slowly drifting light makes one side white-cyan and lets
//            the far side fall to violet.
//   strands  great circles on that sphere, each tilted a little and turning on its own axis, so their
//            projections are ellipses that hug the rim, cross each other and swing in and out like
//            ribbons; the half behind the core is hidden by it, the half in front is lit.
//   core     a dark sphere with a faint rim light, and the eyes.
// Strands and shell are summed additively and tone-mapped softly, so crossings bloom to white without
// clipping. The voice (FFT bands mapped around the circle) pushes the outline; thinking tilts the
// strands further and spins them; working sends a comet along the rim.
//
// Compile: qsb --glsl "100 es,300 es,120,150,330" --hlsl 50 --msl 12 -o orb.frag.qsb orb.frag

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float time;        // phase, seconds scaled by the state's speed (advanced in QML)
    float level;       // smoothed voice level 0..1 (microphone or Jarvis's voice)
    float energy;      // overall brightness of the light 0..1.5
    float deform;      // how far the outline folds 0..1.5
    float twist;       // thinking: strands tilt further and spin, weaving around each other 0..1
    float orbit;       // working: a comet of light travelling the rim 0..1
    float contract;    // 0 awake .. 1 collapsed to a small blob (sleep / hidden)
    float pulse;       // success: brief expansion 0..1
    float attention;   // approval: steady, restrained throb 0..1
    float eyeOpen;     // 0 closed .. 1 open (blinks, sleepy)
    float eyeHappy;    // 0 round eyes .. 1 happy arcs
    float eyeVisible;  // 0..1
    float detail;      // 0 tiny (island pill) .. 1 large (bolder lines when small)
    float pxSize;      // item size in device pixels, for anti-aliasing
    float strands;     // light lines to draw (the rim counts as one), 2..5
    vec2 look;         // where the eyes look, -1..1
    vec4 bandsA;       // 8 spectrum bands 0..1
    vec4 bandsB;
    vec4 colorA;       // electric blue
    vec4 colorB;       // violet
    vec4 highlight;    // the hottest light (cyan)
    vec4 tint;         // state tint; tint.a = how much it takes over
};

const float PI = 3.14159265;
const float TAU = 6.28318531;
const float CORE = 0.70;            // core radius, relative to the shell

float angDist(float a, float b) {
    return mod(a - b + PI, TAU) - PI;
}

// Spectrum around the circle, mirrored so the outline stays closed.
float spectrumBand(int i) {
    // Explicit components also work on the GLSL ES 100 backend: it cannot
    // initialize a local array or dynamically index a uniform vector.
    if (i == 0) return bandsA.x;
    if (i == 1) return bandsA.y;
    if (i == 2) return bandsA.z;
    if (i == 3) return bandsA.w;
    if (i == 4) return bandsB.x;
    if (i == 5) return bandsB.y;
    if (i == 6) return bandsB.z;
    return bandsB.w;
}

float band(float u) {
    float x = abs(fract(u) * 2.0 - 1.0) * 7.0;
    int i = int(floor(x));
    float f = smoothstep(0.0, 1.0, fract(x));
    return mix(spectrumBand(i), spectrumBand(i + 1), f);
}

// The shell's outline at screen angle th: two slow harmonics, an inward fold and an outward lobe that
// travel around in opposite directions, the voice, and the approval throb. 1 = round.
float outline(float th, float t) {
    float d = deform;
    float r = 1.0;
    r += d * 0.040 * sin(2.0 * th + t * 0.53);
    r += d * 0.026 * sin(3.0 * th - t * 0.71 + 1.3);
    float g1 = angDist(th, t * 0.19 + 0.7);
    r -= d * 0.17 * exp(-g1 * g1 * 6.0) * (0.55 + 0.45 * sin(t * 0.43));
    float g2 = angDist(th, -t * 0.13 + 3.1);
    r += d * 0.06 * exp(-g2 * g2 * 4.0) * sin(t * 0.61 + 0.4);
    float b = band(th / TAU + t * 0.015);
    r += level * (0.035 + 0.15 * b) * (0.8 + 0.2 * sin(5.0 * th - t * 2.6));
    r += attention * 0.018 * sin(t * 2.4);
    return r;
}

// Signed distance to one eye: a soft vertical capsule that morphs into a happy arc.
float eyeDist(vec2 q, vec2 c) {
    vec2 h = vec2(0.058, max(0.012, 0.11 * eyeOpen));
    vec2 e = (q - c) / h;
    float round = (length(e) - 1.0) * min(h.x, h.y);
    vec2 a = q - c - vec2(0.0, 0.07);
    float arc = abs(length(a) - 0.085) - 0.022;
    arc = max(arc, a.y + 0.01);              // keep the top half only
    return mix(round, arc, eyeHappy);
}

vec3 tinted(vec3 c) {
    return mix(c, tint.rgb, tint.a);
}

void main() {
    vec2 p = (qt_TexCoord0 - 0.5) * 2.0;    // -1..1 across the item
    float breathe = 1.0 + 0.014 * sin(time * 1.25) * (1.0 - contract);
    float R = mix(0.60, 0.07, contract) * (1.0 + 0.07 * pulse + 0.04 * level) * breathe;
    vec2 q = p / R;                          // shell radius = 1
    float r = length(q);
    float th = atan(q.y, q.x);
    float px = 2.0 / max(pxSize * R, 1.0);   // one device pixel in q units
    float shape = outline(th, time);

    // The light: from the upper side, drifting around slowly.
    float la = time * 0.17 + 2.3;
    vec3 L = normalize(vec3(cos(la), 0.35 + 0.65 * sin(la), 0.6));

    float lineW = mix(0.05, 0.014, detail);                  // half width: bolder when tiny
    float glowW = mix(0.17, 0.09, detail) * (0.8 + 0.4 * energy);
    float coreR = CORE * (1.0 + 0.04 * level);
    float coreMask = 1.0 - smoothstep(coreR - px, coreR + px, r);

    vec3 back = vec3(0.0);    // light behind the core plane (hidden where the core is)
    vec3 front = vec3(0.0);   // light in front of the core

    // ------------------------------------------------------------ shell
    {
        float d = abs(r - shape);
        float lit = max(0.0, dot(vec3(cos(th), sin(th), 0.0), L));
        float hot = lit * lit * lit;
        vec3 c = mix(colorB.rgb, colorA.rgb, smoothstep(0.0, 0.75, lit));
        c = tinted(mix(c, highlight.rgb, hot * 0.85));
        float w = lineW * (0.75 + 0.55 * lit) + px * 0.6;
        float line = 1.0 - smoothstep(w * 0.3, w + px, d);
        float glow = exp(-d / glowW) * 0.46 + 0.0055 / (d + 0.02);
        back += c * (line * 1.45 + glow) * mix(0.28, 1.0, lit);
        // Bloom: wide and faint, warmer toward the light.
        back += tinted(mix(colorB.rgb, colorA.rgb, lit)) * exp(-d / 0.42) * 0.09 * (0.45 + lit);
        // Glass: a faint fill that brightens toward the rim.
        float inside = 1.0 - smoothstep(shape - 0.01, shape + px, r);
        float fres = pow(clamp(r / shape, 0.0, 1.0), 5.0);
        back += tinted(mix(colorB.rgb, colorA.rgb, lit)) * inside * fres * 0.13 * (0.5 + lit);
    }

    // ------------------------------------------------------------ strands (great circles)
    for (int i = 0; i < 4; i++) {
        float k = float(i);
        float present = clamp(strands - 1.0 - k, 0.0, 1.0);
        if (present <= 0.0) break;
        float ph = k * 2.39996;                              // golden angle: they never line up
        float dir = mod(k, 2.0) * 2.0 - 1.0;
        float tilt = 0.36 + 0.16 * sin(time * 0.31 + ph * 1.7) + twist * (0.36 + 0.12 * sin(time * 0.9 + ph));
        float psi = ph + dir * time * (0.16 + 0.05 * k) * (1.0 + 2.2 * twist);
        vec2 w2 = vec2(cos(psi), sin(psi));                  // the tilt's direction (minor axis)
        vec2 m2 = vec2(-w2.y, w2.x);                         // the major axis
        float nz = cos(tilt);
        float a = dot(q, m2);
        float b = dot(q, w2) / nz;
        float L2 = max(length(vec2(a, b)), 1e-4);
        float u = atan(b, a);
        float grad = length(vec2(a, b / nz)) / L2;           // |∇L2|, for a true pixel distance
        float target = shape * (1.0 + 0.028 * (k - 1.0));    // they fan out a little around the rim
        float d = abs(L2 - target) / max(grad, 0.35);
        float z = sin(u) * sin(tilt);                        // depth on the sphere
        vec3 P = vec3(cos(u) * m2 + sin(u) * nz * w2, z);
        float lit = max(0.0, dot(P, L));
        float near = smoothstep(-0.3, 0.3, z);               // the near half is the bright one
        float s = 0.5 + 0.5 * sin(u * (1.0 + mod(k, 2.0)) + dir * time * (0.4 + 0.1 * k) + ph);
        vec3 c = mix(colorA.rgb, colorB.rgb, 0.5 + 0.5 * sin(u + k * 1.3 + time * 0.21));
        c = tinted(mix(c, highlight.rgb, lit * lit * lit * 0.7));
        float w = lineW * 0.8 * (0.55 + 0.6 * near) + px * 0.6;
        float line = 1.0 - smoothstep(w * 0.3, w + px, d);
        float glow = exp(-d / (glowW * 0.8)) * 0.34 + 0.0038 / (d + 0.02);
        vec3 add = c * (line * 1.2 + glow) * mix(0.16, 1.0, near) * mix(0.45, 1.0, s * s) * present;
        if (z >= 0.0) front += add; else back += add;
    }

    // Working: a comet of light runs along the rim, a soft head with a fading tail.
    if (orbit > 0.0) {
        float g = angDist(time * 1.9, th);                   // > 0 behind the head, < 0 ahead of it
        float comet = g >= 0.0 ? exp(-g * 2.4) : exp(-g * g * 120.0);
        float d = abs(r - shape);
        vec3 c = tinted(mix(highlight.rgb, vec3(1.0), 0.5));
        back += c * comet * (exp(-d / (lineW * 2.0 + px)) * 1.1 + exp(-d * d / (glowW * glowW)) * 0.35) * orbit;
    }

    // The core hides what is behind it; light in front of it stays, softened so the face reads.
    vec3 light = back * (1.0 - coreMask * 0.92) + front * (1.0 - coreMask * 0.45);
    light *= energy;
    light = vec3(1.0) - exp(-light * 1.2);                   // soft tone curve: overlaps bloom to white
    light *= smoothstep(1.0, 0.8, length(p));                // never clip at the item's edge

    // ------------------------------------------------------------ core
    vec3 core = mix(vec3(0.004, 0.005, 0.016), vec3(0.018, 0.02, 0.06), smoothstep(0.0, coreR, r));
    float litc = max(0.0, dot(normalize(vec3(cos(th), sin(th), 0.3)), L));
    float edge = pow(smoothstep(coreR * 0.5, coreR, r), 3.0);
    core += tinted(mix(colorB.rgb, colorA.rgb, litc)) * edge * (0.10 + 0.28 * litc) * energy;

    // ------------------------------------------------------------ eyes
    vec3 eyes = vec3(0.0);
    if (eyeVisible > 0.0) {
        vec2 off = look * vec2(0.075, 0.06);
        float de = min(eyeDist(q, vec2(-0.19, 0.0) + off), eyeDist(q, vec2(0.19, 0.0) + off));
        float body = 1.0 - smoothstep(-px, px, de);
        float halo = exp(-max(de, 0.0) / 0.045) * 0.42;
        vec3 ec = mix(vec3(0.88, 0.94, 1.0), tint.rgb, tint.a * 0.35);
        eyes = ec * (body + halo) * eyeVisible * coreMask;
    }

    vec3 rgb = core * coreMask + eyes + light;
    float alpha = clamp(max(coreMask, max(light.r, max(light.g, light.b))), 0.0, 1.0);
    rgb = min(rgb, vec3(alpha));                              // premultiplied
    fragColor = vec4(rgb, alpha) * qt_Opacity;
}
