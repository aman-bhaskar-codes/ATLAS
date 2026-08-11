// Interactive architecture diagram
// Shows tooltips on hover, links to source files
(function() {
  const svg = document.querySelector('#architecture-diagram');
  if (!svg) return;
  
  const components = svg.querySelectorAll('.component');
  const tooltip = svg.querySelector('#tooltip');
  const tooltipText = svg.querySelector('#tooltip-text');
  
  components.forEach(component => {
    component.addEventListener('mouseenter', (e) => {
      const info = component.getAttribute('data-info');
      if (!info || !tooltip || !tooltipText) return;
      
      tooltipText.textContent = info;
      tooltip.style.display = 'block';
      
      // Position tooltip near cursor
      const rect = component.getBoundingClientRect();
      tooltip.setAttribute('transform', `translate(${rect.x + 10}, ${rect.y - 70})`);
    });
    
    component.addEventListener('mouseleave', () => {
      if (tooltip) tooltip.style.display = 'none';
    });
    
    // Optional: click to navigate to source file
    component.addEventListener('click', () => {
      const file = component.getAttribute('data-file');
      if (file) {
        console.log(`Navigate to: ${file}`);
        // In a real implementation, this could open the file in GitHub
      }
    });
  });
})();
