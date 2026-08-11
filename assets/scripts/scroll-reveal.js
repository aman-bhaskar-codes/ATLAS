// Scroll-triggered fade-in animations
// Respects prefers-reduced-motion
(function() {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    // Add all elements immediately without animation
    document.querySelectorAll('[data-scroll-reveal]').forEach(el => {
      el.style.opacity = '1';
      el.style.transform = 'none';
    });
    return;
  }
  
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('revealed');
        // Unobserve after reveal to improve performance
        observer.unobserve(entry.target);
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  });
  
  // Observe all elements with data-scroll-reveal attribute
  document.querySelectorAll('[data-scroll-reveal]').forEach(el => {
    observer.observe(el);
  });
})();
