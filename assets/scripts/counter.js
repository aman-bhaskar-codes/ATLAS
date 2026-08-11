// Animated number counter with easing
// Respects prefers-reduced-motion
(function() {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
  function animateCounter(element) {
    const target = parseInt(element.getAttribute('data-target'));
    const duration = reducedMotion ? 0 : 2000;
    const start = 0;
    const startTime = performance.now();
    
    function easeOutQuart(x) {
      return 1 - Math.pow(1 - x, 4);
    }
    
    function update(currentTime) {
      const elapsed = currentTime - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const value = Math.floor(start + (target - start) * easeOutQuart(progress));
      
      element.textContent = value.toLocaleString();
      
      if (progress < 1) {
        requestAnimationFrame(update);
      }
    }
    
    if (reducedMotion) {
      element.textContent = target.toLocaleString();
    } else {
      requestAnimationFrame(update);
    }
  }
  
  // Observe counters and animate when visible
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        animateCounter(entry.target);
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.5 });
  
  document.querySelectorAll('[data-counter]').forEach(el => {
    el.textContent = '0';
    observer.observe(el);
  });
})();
