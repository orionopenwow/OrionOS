// OrionOS Gravity mode: Hyprland screen shader (decoration:screen_shader). Off by default.
// The whole desktop is lensed around the black hole at the centre of the screen: windows near the core stretch and
// bend toward it, the screen edges stay exactly flat. It is a soft lens (it never folds or tears the picture, and
// nothing is hidden), with a faint violet rim where space starts to fall in.
//
// orion-gravity writes a copy with this screen's shape filled in (ASPECT) to ~/.cache/orion/gravity.frag and points
// Hyprland at it; it switches the shader off on battery and when reduced motion is on.
// GLSL ES 1.00 form, which every Hyprland version with screen shaders accepts.
precision highp float;
varying vec2 v_texcoord;
uniform sampler2D tex;

const float ASPECT = 1.7778;   // width / height; replaced by orion-gravity
const float CORE = 0.20;       // radius of the core's pull, in screen heights
const float STRENGTH = 0.55;   // how hard space falls in (same as the HUD's depth layer)
const float RIM = 0.62;        // beyond this distance (screen heights) the screen is untouched

void main() {
    vec2 d = (v_texcoord - 0.5) * vec2(ASPECT, 1.0);
    float r = length(d);
    // lens-like pull toward the centre, softened inside the core so it never folds over
    float lens = STRENGTH * CORE * CORE / sqrt(r * r + CORE * CORE * 0.6);
    float t = clamp((r - RIM * 0.35) / (RIM * 0.65), 0.0, 1.0);
    float flat_ = 1.0 - t * t * (3.0 - 2.0 * t);
    float pull = min(r * 0.85, lens * flat_);
    // sample nearer the centre than we are: the middle of the screen is magnified and bent inward
    vec2 src = r > 1e-5 ? d * (1.0 - pull / r) : d;
    vec2 uv = src / vec2(ASPECT, 1.0) + 0.5;
    vec4 c = texture2D(tex, uv);
    // a faint violet rim where the well begins (Accretion #7C5CFF), and a slight darkening down the slope
    float rim = exp(-pow((r - CORE * 1.15) / (CORE * 0.35), 2.0)) * 0.05 * flat_;
    c.rgb = c.rgb * (1.0 - 0.10 * flat_ * smoothstep(RIM, CORE, r)) + vec3(0.486, 0.361, 1.0) * rim;
    gl_FragColor = c;
}
