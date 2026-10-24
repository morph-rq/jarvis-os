#version 450
// Caelestia's transition fragments use ES 300; Qt's built-in vertex uses ES 100.
// Bake with qsb --glsl '300 es,330' -o wallpaper.vert.qsb wallpaper.vert
layout(location = 0) in vec4 qt_Vertex;
layout(location = 1) in vec2 qt_MultiTexCoord0;
layout(location = 0) out vec2 qt_TexCoord0;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
};
void main() {
    qt_TexCoord0 = qt_MultiTexCoord0;
    gl_Position = qt_Matrix * qt_Vertex;
}
