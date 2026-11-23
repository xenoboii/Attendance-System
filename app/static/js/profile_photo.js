(() => {
  const modal = document.getElementById('profile-photo-modal');
  if (!modal) return;
  const input = modal.querySelector('input[type="file"]');
  const preview = modal.querySelector('[data-photo-preview]');
  const editor = modal.querySelector('[data-photo-crop]');
  const stage = modal.querySelector('[data-crop-stage]');
  const cropImage = modal.querySelector('[data-crop-image]');
  const zoomInput = modal.querySelector('[data-crop-zoom]');
  const zoomValue = modal.querySelector('[data-crop-zoom-value]');
  const saveButton = modal.querySelector('[data-photo-save]');
  const status = modal.querySelector('[data-photo-status]');
  const fields = ['crop_x', 'crop_y', 'crop_size'].map(name => modal.querySelector(`[name="${name}"]`));
  const pointers = new Map();
  const clamp = (value, min, max) => Math.max(min, Math.min(value, max));
  let objectUrl, image, centerX, centerY, zoom = 1, gesture;

  function resetEditor() {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    image = null;
    gesture = null;
    pointers.clear();
    cropImage.removeAttribute('src');
    editor.hidden = true;
    preview.hidden = false;
    saveButton.disabled = true;
    status.textContent = '';
    fields.forEach(field => { field.disabled = true; field.value = ''; });
  }

  function crop() {
    const side = Math.min(image.naturalWidth, image.naturalHeight) / zoom;
    centerX = clamp(centerX, side / 2, image.naturalWidth - side / 2);
    centerY = clamp(centerY, side / 2, image.naturalHeight - side / 2);
    return {side, left: centerX - side / 2, top: centerY - side / 2};
  }

  function render() {
    if (!image || editor.hidden) return;
    const box = crop();
    const scale = stage.getBoundingClientRect().width / box.side;
    cropImage.style.width = `${image.naturalWidth * scale}px`;
    cropImage.style.height = `${image.naturalHeight * scale}px`;
    cropImage.style.left = `${-box.left * scale}px`;
    cropImage.style.top = `${-box.top * scale}px`;
    fields[0].value = (box.left / image.naturalWidth).toFixed(10);
    fields[1].value = (box.top / image.naturalHeight).toFixed(10);
    fields[2].value = (1 / zoom).toFixed(10);
    zoomInput.value = zoom;
    zoomValue.textContent = `${zoom.toFixed(2).replace(/\.?0+$/, '')}×`;
  }

  function resetCrop() {
    if (!image) return;
    zoom = 1;
    centerX = image.naturalWidth / 2;
    centerY = image.naturalHeight / 2;
    render();
  }

  function fail(message) {
    resetEditor();
    input.setCustomValidity(message);
    status.textContent = message;
  }

  input.addEventListener('change', () => {
    resetEditor();
    input.setCustomValidity('');
    input.classList.remove('is-invalid');
    const oldError = modal.querySelector('#profile-photo-error');
    if (oldError) oldError.hidden = true;
    const file = input.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) return fail('Choose a picture smaller than 2 MB.');
    if (file.type && !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      return fail('Use a JPG, PNG or WEBP picture.');
    }
    objectUrl = URL.createObjectURL(file);
    const loadingUrl = objectUrl;
    const selected = new Image();
    status.textContent = 'Loading picture…';
    selected.onload = () => {
      if (objectUrl !== loadingUrl) return;
      if (!selected.naturalWidth || !selected.naturalHeight) return fail('That picture could not be read.');
      if (selected.naturalWidth * selected.naturalHeight > 12000000) {
        return fail('Crop or resize your picture to 12 megapixels or less.');
      }
      image = selected;
      cropImage.src = loadingUrl;
      preview.hidden = true;
      editor.hidden = false;
      fields.forEach(field => { field.disabled = false; });
      status.textContent = '';
      resetCrop();
      saveButton.disabled = false;
    };
    selected.onerror = () => {
      if (objectUrl === loadingUrl) fail('That picture could not be read. Try another JPG, PNG or WEBP.');
    };
    selected.src = loadingUrl;
  });

  zoomInput.addEventListener('input', () => {
    zoom = Number(zoomInput.value);
    render();
  });
  modal.querySelector('[data-crop-reset]').addEventListener('click', resetCrop);

  function startGesture() {
    gesture = null;
    if (!image) return;
    const points = [...pointers.values()];
    if (points.length === 1) gesture = {type: 'pan', last: points[0]};
    if (points.length >= 2) {
      const [a, b] = points;
      const rect = stage.getBoundingClientRect();
      const box = crop();
      gesture = {
        type: 'pinch', zoom,
        distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
        anchorX: box.left + ((a.x + b.x) / 2 - rect.left) * box.side / rect.width,
        anchorY: box.top + ((a.y + b.y) / 2 - rect.top) * box.side / rect.height,
      };
    }
  }

  stage.addEventListener('pointerdown', event => {
    if (!image || (event.pointerType === 'mouse' && event.button !== 0)) return;
    event.preventDefault();
    stage.focus({preventScroll: true});
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    stage.setPointerCapture(event.pointerId);
    startGesture();
  });
  stage.addEventListener('pointermove', event => {
    if (!image || !pointers.has(event.pointerId)) return;
    const point = {x: event.clientX, y: event.clientY};
    pointers.set(event.pointerId, point);
    if (gesture?.type === 'pan') {
      const scale = stage.getBoundingClientRect().width / crop().side;
      centerX -= (point.x - gesture.last.x) / scale;
      centerY -= (point.y - gesture.last.y) / scale;
      gesture.last = point;
    } else if (gesture?.type === 'pinch') {
      const [a, b] = [...pointers.values()];
      const rect = stage.getBoundingClientRect();
      zoom = clamp(gesture.zoom * Math.hypot(a.x - b.x, a.y - b.y) / gesture.distance, 1, 4);
      const side = Math.min(image.naturalWidth, image.naturalHeight) / zoom;
      centerX = gesture.anchorX - ((a.x + b.x) / 2 - rect.left) * side / rect.width + side / 2;
      centerY = gesture.anchorY - ((a.y + b.y) / 2 - rect.top) * side / rect.height + side / 2;
    }
    render();
  });
  function endPointer(event) {
    pointers.delete(event.pointerId);
    startGesture();
  }
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => stage.addEventListener(name, endPointer));
  stage.addEventListener('keydown', event => {
    if (!image) return;
    const step = crop().side * (event.shiftKey ? .1 : .02);
    const directions = {ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step]};
    if (!directions[event.key]) return;
    event.preventDefault();
    centerX += directions[event.key][0];
    centerY += directions[event.key][1];
    render();
  });
  new ResizeObserver(render).observe(stage);
  modal.addEventListener('hidden.bs.modal', () => {
    input.value = '';
    input.setCustomValidity('');
    resetEditor();
  });
})();
