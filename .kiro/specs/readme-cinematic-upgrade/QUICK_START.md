# Cinematic README — Quick Start Guide

## 🎬 How to View the Animations

### Option 1: GitHub (Partial Support)
**What works:**
- ✅ Animated SVGs (OTAR loop, architecture)
- ✅ CSS animations (3D terminal, badges)
- ✅ Inline styles (comparison table)

**What doesn't work:**
- ❌ Particle background (JS blocked)
- ❌ Scroll reveals (JS blocked)
- ❌ Animated counters (JS blocked)
- ❌ Interactive tooltips (JS blocked)

**How:** Just view `README.md` on GitHub — 70% of animations visible!

---

### Option 2: Local Preview (Full Support)
**All animations work!**

```bash
# Install grip (GitHub README preview)
pip install grip

# Preview README
cd /path/to/ATLAS/atlas
grip README.md

# Open http://localhost:6419
```

**What you'll see:**
- ✅ Particle background drifting
- ✅ Counters animating (149 → count up)
- ✅ Sections fading in as you scroll
- ✅ Architecture tooltips on hover
- ✅ Full CSS animations

---

### Option 3: Deploy Docs Site (Recommended)
For production-grade viewing:

```bash
# Option A: MkDocs Material
pip install mkdocs-material
mkdocs new atlas-docs
# Copy README.md to docs/index.md
mkdocs serve

# Option B: Vercel
npm install -g vercel
# Convert README.md to index.html
vercel deploy
```

---

## 🎥 Recording Demo GIFs

### Prerequisites
```bash
# Install tools
brew install asciinema
cargo install --git https://github.com/asciinema/agg
```

### Recording Workflow

#### 1. Task Execution Demo
```bash
# Set terminal size
export COLUMNS=100 LINES=30

# Start recording
asciinema rec task-execution.cast

# Run command
uv run atlas run "research the latest papers on transformers"

# Stop recording (Ctrl+D)

# Convert to GIF
agg --speed 1.5 --fps-cap 60 task-execution.cast assets/demos/task-execution.gif

# Optimize
gifsicle -O3 --lossy=80 assets/demos/task-execution.gif -o assets/demos/task-execution.gif
```

#### 2. Safety Intercept Demo
```bash
asciinema rec safety-intercept.cast
uv run atlas run "delete all files in /tmp"
# Watch the approval flow + confirmation code
agg --speed 1.5 --fps-cap 60 safety-intercept.cast assets/demos/safety-intercept.gif
```

#### 3. Multi-Agent DAG Demo
```bash
asciinema rec multi-agent-dag.cast
uv run atlas run "research quantum computing, write a blog post about it, and create a Python code example"
agg --speed 1.5 --fps-cap 60 multi-agent-dag.cast assets/demos/multi-agent-dag.gif
```

#### 4. Memory Retrieval Demo
```bash
# First, set a preference
uv run atlas run "I prefer Python over JavaScript"

# Then test memory influence
asciinema rec memory-retrieval.cast
uv run atlas run "write a script to fetch data from an API"
# Watch it choose Python due to memory
agg --speed 1.5 --fps-cap 60 memory-retrieval.cast assets/demos/memory-retrieval.gif
```

### Target Specs
- **Duration:** 7-12 seconds each
- **Size:** < 2 MB
- **FPS:** 60
- **Resolution:** 100 cols × 30 rows (small, readable)

---

## 🎨 Customizing Animations

### Change Colors
Edit `assets/styles/readme-animations.css`:

```css
:root {
  --atlas-blue: #58a6ff;   /* Change to your brand color */
  --atlas-gold: #d29922;   /* Change highlight color */
}
```

### Adjust Animation Speed
```css
/* Slower particles */
.flow-line {
  animation: flow 8s linear infinite; /* was 4s */
}

/* Faster scroll reveals */
[data-scroll-reveal] {
  transition: opacity 0.3s ease-out; /* was 0.6s */
}
```

### Disable Specific Animations
```css
/* No particle background */
#particles-canvas {
  display: none;
}

/* No badge pulse */
.badge-pulse {
  animation: none;
}
```

---

## 🐛 Troubleshooting

### "Animations not working on GitHub"
**Solution:** GitHub blocks `<script>` tags. Use local preview or deploy docs site.

### "Particle canvas is blank"
**Check:**
1. Is JavaScript enabled?
2. Is the canvas element present? (`<canvas id="particles-canvas">`)
3. Is `particles.js` loaded? (Check browser console)

### "Scroll reveals happen instantly"
**Cause:** `prefers-reduced-motion` is enabled (accessibility feature)
**Solution:** This is intentional! Users with motion sensitivity see content immediately.

### "Counter shows 0 instead of 149"
**Check:**
1. Is `counter.js` loaded?
2. Does the element have `data-counter` and `data-target="149"`?
3. Check browser console for errors

### "3D terminal looks flat"
**Cause:** Browser doesn't support CSS transforms
**Solution:** Use a modern browser (Chrome, Firefox, Safari, Edge)

---

## 📊 Performance Tips

### Reduce Motion for Accessibility
Add this to your site:

```html
<meta name="color-scheme" content="dark light">
<style>
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>
```

Already included in `readme-animations.css` ✅

### Optimize GIFs
```bash
# Install gifsicle
brew install gifsicle

# Compress GIF
gifsicle -O3 --lossy=80 input.gif -o output.gif

# Check size
ls -lh output.gif  # Should be < 2 MB
```

### Lazy Load Images
```html
<img src="demo.gif" loading="lazy" alt="Demo" />
```

---

## 🎓 Best Practices

### 1. Keep Animations Subtle
- ✅ 10% opacity particle background
- ✅ Gentle pulse (scale 1 → 1.05)
- ❌ Spinning elements, flashing colors

### 2. Respect User Preferences
- ✅ `prefers-reduced-motion` support
- ✅ `prefers-color-scheme` support
- ✅ No autoplay videos with sound

### 3. Progressive Enhancement
- ✅ Content works without JS
- ✅ Animations enhance, don't block
- ✅ Graceful degradation

### 4. Performance First
- ✅ < 10 KB JavaScript
- ✅ CSS animations (hardware-accelerated)
- ✅ `defer` script loading
- ✅ IntersectionObserver (only animate visible elements)

---

## 📚 References

### Tools Used
- [Asciinema](https://asciinema.org) — Terminal recording
- [agg](https://github.com/asciinema/agg) — GIF conversion
- [gifsicle](https://www.lcdf.org/gifsicle/) — GIF optimization
- [grip](https://github.com/joeyespo/grip) — Local README preview

### Inspiration
- [readme-typing-svg](https://github.com/DenverCoder1/readme-typing-svg)
- [capsule-render](https://github.com/kyechan99/capsule-render)
- [shields.io](https://shields.io) — Badges

### MDN Resources
- [CSS Animations](https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_Animations)
- [SVG Animation](https://developer.mozilla.org/en-US/docs/Web/SVG/SVG_animation_with_SMIL)
- [IntersectionObserver](https://developer.mozilla.org/en-US/docs/Web/API/Intersection_Observer_API)
- [prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion)

---

## ✨ What's Next?

### Immediate
1. Record 4 demo GIFs (see commands above)
2. Test on mobile devices
3. Share on social media 🐦

### Future Enhancements
- [ ] Dark/light theme toggle
- [ ] Interactive architecture diagram (click to expand)
- [ ] Real-time stats API (live test count)
- [ ] Video demos (YouTube embeds)
- [ ] Contribution graph (GitHub API)

---

**Ready to see your cinematic README in action?** 🎬  
Run `grip README.md` and open http://localhost:6419
