(() => {
  'use strict';

  const canvas = document.getElementById('treeCanvas');
  const ctx = canvas.getContext('2d');

  const ageEl = document.querySelector('[data-age]');
  const moodEl = document.querySelector('[data-mood]');
  const stageEl = document.querySelector('[data-stage]');
  const fpsEl = document.querySelector('[data-fps]');
  const hintEl = document.querySelector('[data-hint]');
  const waterBtn = document.getElementById('waterBtn');

  const CONFIG = {
    maxDepth: 7,
    growthDelayPerLevel: 0.11,
    baseGrowthSpeed: 0.00022,
    pulseBoost: 0.04,
    sawRadius: 28,
    sawCooldown: 140,
    fruitDepthStart: 4,
    saplingMin: 2,
    saplingMax: 4
  };

  const stageDescriptions = {
    seed: 'Семя набирает силу',
    sprout: 'Молодые побеги',
    young: 'Гибкое дерево',
    mature: 'Пышная крона',
    fruit: 'Созревание плодов',
    decline: 'Осенний листопад',
    seedFall: 'Сон и семена',
    rebirth: 'Новое поколение'
  };

  const stageHints = {
    seed: 'Полей семя и жди, как оно тянется вверх.',
    sprout: 'Рост медленный, но заметный — смотри за верхушкой.',
    young: 'Ветви реагируют на ветер, можно подрезать лишнее пилой.',
    mature: 'Дерево дышит полной кроной — следи за формой.',
    fruit: 'Плоды наливаются соком, дождись их свечения.',
    decline: 'Наступает осень, листья теплеют и падают.',
    seedFall: 'Семена укореняются, скоро взойдут саженцы.',
    rebirth: 'Молодые саженцы готовят новую жизнь дерева.'
  };

  let width = 0;
  let height = 0;
  let tree = null;
  let branchIdCounter = 0;
  let growth = 0.015;
  let targetGrowth = 0.08;
  let hydration = 0;
  let lastTime = performance.now();
  let deltaMs = 16;
  let simulatedAge = 0;
  let wind = 0;
  let windTarget = 0;
  let season = Math.random() * Math.PI * 2;
  let fps = 60;
  let lifeStage = 'seed';
  let stageTimer = 0;
  let regenCountdown = 0;
  let fruitsRipened = 0;

  let branchSegments = [];
  let saplings = [];
  let sawdust = [];
  let sawTrail = [];
  let floatingLights = [];
  let mountains = [];
  let birds = [];
  let clouds = [];
  let stars = [];

  const sawState = { active: false, x: 0, y: 0, lastCut: 0 };

  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);
  const seededRandom = (seed) => (Math.sin(seed * 9341.13) + 1) / 2;

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    tree = buildTree();
    floatingLights = createFireflies(26);
    mountains = buildMountains();
    clouds = buildClouds();
    birds = createBirds(8);
    stars = createStars(110);
    sawState.x = width / 2;
    sawState.y = height / 2;
  }

  window.addEventListener('resize', resize);
  resize();

  function buildTree() {
    branchIdCounter = 0;
    const baseLength = Math.min(width, height) * 0.25;
    return createBranch(0, baseLength, 0, Math.random() * 1000);
  }

  function createBranch(depth, length, angle, seed) {
    const branch = {
      id: branchIdCounter++,
      depth,
      length,
      angle,
      seed,
      children: [],
      swayPhase: Math.random() * Math.PI * 2,
      baseWidth: Math.max(1, (CONFIG.maxDepth - depth + 1) * 1.15),
      color: {
        r: 82 + depth * 5 + seededRandom(seed + 6) * 25,
        g: 58 + depth * 3 + seededRandom(seed + 7) * 18,
        b: 42 + depth * 2 + seededRandom(seed + 8) * 18
      },
      pruned: false,
      regrowAt: 0,
      pruneLength: length * (0.2 + seededRandom(seed + 12) * 0.2),
      fruits: null
    };

    if (depth >= CONFIG.maxDepth) {
      return branch;
    }

    const spread = 0.25 + seededRandom(seed + 1) * 0.35;
    const lenFactor = 0.68 + seededRandom(seed + 2) * 0.1 - depth * 0.01;
    const lean = (seededRandom(seed + 3) - 0.5) * 0.18;

    branch.children.push(
      createBranch(depth + 1, length * lenFactor, angle + spread + lean, seed * 1.71 + 1)
    );
    branch.children.push(
      createBranch(depth + 1, length * lenFactor, angle - spread + lean, seed * 1.71 + 2)
    );

    if (depth > 1 && seededRandom(seed + 4) > 0.58) {
      const variance = (seededRandom(seed + 5) - 0.5) * spread;
      branch.children.push(
        createBranch(depth + 1, length * (lenFactor * 0.9), angle + variance, seed * 1.71 + 3)
      );
    }

    return branch;
  }

  function createFireflies(count) {
    return Array.from({ length: count }).map(() => ({
      x: Math.random(),
      y: Math.random(),
      scale: 0.6 + Math.random() * 1.4,
      speed: 0.2 + Math.random() * 0.6,
      hue: 80 + Math.random() * 40,
      seed: Math.random() * 10
    }));
  }

  function createStars(count) {
    return Array.from({ length: count }).map(() => ({
      x: Math.random(),
      y: Math.random(),
      size: 0.5 + Math.random() * 1.5,
      speed: 0.0005 + Math.random() * 0.001,
      phase: Math.random() * Math.PI * 2
    }));
  }

  function buildMountains() {
    return Array.from({ length: 3 }).map((_, index) => {
      const offsetY = height * (0.45 + index * 0.08);
      const amplitude = 60 + index * 40;
      const segments = 16;
      const points = [];
      for (let i = 0; i <= segments; i += 1) {
        const t = i / segments;
        const x = t * width;
        const noise =
          Math.sin(t * Math.PI * 2 + index) * amplitude * 0.35 +
          Math.sin(t * 6 + index * 3) * amplitude * 0.15;
        const y = offsetY + noise;
        points.push({ x, y });
      }
      return {
        points,
        color: `rgba(${25 + index * 15}, ${40 + index * 18}, ${70 + index * 25}, ${0.35 + index * 0.15})`,
        parallax: 0.05 + index * 0.04,
        offset: Math.random() * 800
      };
    });
  }

  function buildClouds() {
    return Array.from({ length: 6 }).map(() => ({
      x: Math.random() * width,
      y: height * (0.08 + Math.random() * 0.2),
      speed: 10 + Math.random() * 25,
      scale: 0.6 + Math.random() * 1.2,
      opacity: 0.2 + Math.random() * 0.2
    }));
  }

  function createBirds(count) {
    return Array.from({ length: count }).map(() => spawnBird());
  }

  function spawnBird() {
    return {
      x: Math.random() * width,
      y: height * (0.15 + Math.random() * 0.35),
      speed: 35 + Math.random() * 45,
      direction: Math.random() > 0.5 ? 1 : -1,
      amplitude: 6 + Math.random() * 10,
      phase: Math.random() * Math.PI * 2,
      layer: 0.4 + Math.random() * 0.4
    };
  }

  function updateFireflies(delta) {
    floatingLights.forEach((light) => {
      light.y -= (light.speed * delta) / 8000;
      light.x += Math.sin(season + light.seed) * 0.0002 * delta;
      if (light.y < -0.05) {
        light.y = 1.1;
        light.x = Math.random();
      }
    });
  }

  function updateClouds(delta) {
    clouds.forEach((cloud) => {
      cloud.x += cloud.speed * delta * 0.0001;
      if (cloud.x > width + 200) {
        cloud.x = -200;
      }
    });
  }

  function updateBirds(delta) {
    birds.forEach((bird, idx) => {
      bird.phase += delta * 0.002;
      bird.x += bird.speed * bird.direction * delta * 0.0003;
      bird.y += Math.sin(bird.phase) * bird.amplitude * 0.02;
      if (bird.direction > 0 && bird.x > width + 40) {
        birds[idx] = spawnBird();
        birds[idx].x = -40;
        birds[idx].direction = 1;
      } else if (bird.direction < 0 && bird.x < -40) {
        birds[idx] = spawnBird();
        birds[idx].x = width + 40;
        birds[idx].direction = -1;
      }
    });
  }

  function updateSaplings(delta) {
    saplings.forEach((sapling) => {
      sapling.growth = clamp(sapling.growth + delta * 0.00012, 0, 1);
      sapling.phase += delta * 0.001;
    });
  }

  function drawSaplings(time) {
    saplings.forEach((sapling) => {
      const swayOffset = Math.sin(time * 0.001 + sapling.phase) * sapling.sway;
      const heightScale = sapling.growth * 40;
      ctx.strokeStyle = 'rgba(92, 140, 92, 0.8)';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sapling.x, sapling.y);
      ctx.quadraticCurveTo(
        sapling.x + swayOffset,
        sapling.y - heightScale * 0.5,
        sapling.x + swayOffset * 1.2,
        sapling.y - heightScale
      );
      ctx.stroke();
      ctx.fillStyle = 'rgba(122, 200, 122, 0.6)';
      ctx.beginPath();
      ctx.arc(sapling.x + swayOffset * 1.2, sapling.y - heightScale, 4 + sapling.growth * 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function spawnSaplings() {
    const fruitBonus = Math.min(3, Math.floor(fruitsRipened / 6));
    const randomCount =
      CONFIG.saplingMin + Math.random() * (CONFIG.saplingMax - CONFIG.saplingMin + 1);
    const count = Math.max(CONFIG.saplingMin, Math.floor(randomCount + fruitBonus));
    fruitsRipened = Math.max(0, fruitsRipened - fruitBonus * 3);
    saplings = Array.from({ length: count }).map(() => ({
      x: width / 2 + (Math.random() - 0.5) * width * 0.18,
      y: height - 45 + Math.random() * 10,
      growth: 0.02,
      phase: Math.random() * Math.PI * 2,
      sway: 0.4 + Math.random() * 0.6
    }));
  }

  function setStage(nextStage) {
    if (lifeStage === nextStage) {
      return;
    }
    lifeStage = nextStage;
    stageTimer = 0;
  }

  function updateLifeStage(delta) {
    switch (lifeStage) {
      case 'seed':
        if (growth > 0.18) setStage('sprout');
        break;
      case 'sprout':
        if (growth > 0.42) setStage('young');
        break;
      case 'young':
        if (growth > 0.66) setStage('mature');
        break;
      case 'mature':
        if (growth > 0.82 && stageTimer > 6000) setStage('fruit');
        break;
      case 'fruit':
        if (stageTimer > 32000 || Math.random() < delta * 0.0000006) {
          setStage('decline');
        }
        break;
      case 'decline':
        if (growth < 0.3) {
          setStage('seedFall');
        }
        break;
      case 'seedFall':
        regenCountdown += delta;
        if (!saplings.length) {
          spawnSaplings();
        }
        if (regenCountdown > 18000) {
          setStage('rebirth');
        }
        break;
      case 'rebirth':
        if (!saplings.length) {
          spawnSaplings();
        }
        if (saplings.every((sapling) => sapling.growth > 0.98)) {
          tree = buildTree();
          growth = 0.05;
          targetGrowth = 0.15;
          saplings = [];
          setStage('sprout');
        }
        break;
      default:
        break;
    }

    if (lifeStage !== 'seedFall' && lifeStage !== 'rebirth') {
      regenCountdown = 0;
    }

    if (lifeStage === 'decline') {
      targetGrowth = Math.max(0.24, targetGrowth - delta * 0.00012);
    }
  }

  function drawSky(time) {
    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    const dawn = 0.2 + Math.sin(season * 0.6) * 0.1;
    gradient.addColorStop(0, `rgba(${20 + dawn * 120}, ${40 + dawn * 80}, ${110 + dawn * 50}, 1)`);
    gradient.addColorStop(0.55, '#09152f');
    gradient.addColorStop(1, '#030711');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, width, height);

    stars.forEach((star) => {
      const twinkle = 0.5 + Math.sin(time * star.speed + star.phase) * 0.4;
      ctx.fillStyle = `rgba(255, 255, 255, ${0.15 + twinkle * 0.2})`;
      ctx.fillRect(star.x * width, star.y * height * 0.5, star.size, star.size);
    });
  }

  function drawMountains(time) {
    mountains.forEach((layer, index) => {
      ctx.fillStyle = layer.color;
      ctx.beginPath();
      ctx.moveTo(0, height);
      layer.points.forEach((point, idx) => {
        const wave = Math.sin(time * 0.00002 + idx * 0.5 + layer.offset) * 8 * (index + 1) * 0.2;
        ctx.lineTo(point.x, point.y + wave);
      });
      ctx.lineTo(width, height);
      ctx.closePath();
      ctx.fill();
    });
  }

  function drawCloudsLayer() {
    clouds.forEach((cloud) => {
      ctx.fillStyle = `rgba(210, 230, 255, ${cloud.opacity})`;
      ctx.beginPath();
      ctx.ellipse(cloud.x, cloud.y, 120 * cloud.scale, 40 * cloud.scale, 0, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawFirefliesLayer() {
    floatingLights.forEach((light) => {
      const x = light.x * width;
      const y = light.y * height;
      ctx.beginPath();
      ctx.fillStyle = `hsla(${light.hue}, 80%, 70%, 0.35)`;
      ctx.arc(x, y, light.scale * 2.2, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function drawBirdsLayer(time) {
    birds.forEach((bird) => {
      ctx.save();
      ctx.translate(bird.x, bird.y + Math.sin(time * 0.001 + bird.phase) * bird.amplitude);
      ctx.scale(bird.direction, 1);
      ctx.strokeStyle = `rgba(255, 255, 255, ${0.25 + bird.layer * 0.25})`;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.moveTo(-8, 0);
      ctx.quadraticCurveTo(0, -4, 8, 0);
      ctx.stroke();
      ctx.restore();
    });
  }

  function drawGround() {
    ctx.save();
    ctx.translate(width / 2, height - 35);
    const grd = ctx.createRadialGradient(0, 0, 40, 0, 0, Math.max(width, 500));
    grd.addColorStop(0, 'rgba(34, 70, 38, 0.8)');
    grd.addColorStop(0.6, 'rgba(14, 32, 24, 0.95)');
    grd.addColorStop(1, 'rgba(4, 8, 12, 1)');
    ctx.fillStyle = grd;
    ctx.beginPath();
    ctx.ellipse(0, 0, width * 0.55, 190, 0, 0, Math.PI, true);
    ctx.fill();
    ctx.restore();
  }

  function drawTree(time) {
    if (!tree) return;
    branchSegments.length = 0;
    drawBranch(tree, time, 0, width / 2, height - 60);
  }

  function drawBranch(branch, time, parentAngle, startX, startY) {
    const depthDelay = branch.depth * CONFIG.growthDelayPerLevel;
    const available = 1 - depthDelay;
    const progress = clamp((growth - depthDelay) / available, 0, 1);

    if (progress <= 0) {
      return;
    }

    const eased = easeOutCubic(progress);
    const sway =
      Math.sin(time * 0.0009 + branch.swayPhase + branch.seed) *
      (0.03 + (CONFIG.maxDepth - branch.depth) * 0.005) *
      (0.35 + growth * 0.65);
    const windGust = wind * (0.25 + branch.depth * 0.04);
    const angle = parentAngle + branch.angle + sway + windGust;
    const length = branch.length * eased;
    const dryness = lifeStage === 'decline' || lifeStage === 'seedFall' ? 0.72 : 1;
    const endX = startX + Math.sin(angle) * length;
    const endY = startY - Math.cos(angle) * length;

    if (branch.pruned) {
      if (time > branch.regrowAt) {
        branch.pruned = false;
      } else {
        const stubLength = Math.min(branch.pruneLength, length * 0.6);
        const stubX = startX + Math.sin(angle) * stubLength;
        const stubY = startY - Math.cos(angle) * stubLength;
        ctx.strokeStyle = 'rgba(74, 58, 46, 0.95)';
        ctx.lineWidth = branch.baseWidth;
        ctx.beginPath();
        ctx.moveTo(startX, startY);
        ctx.lineTo(stubX, stubY);
        ctx.stroke();
        ctx.fillStyle = 'rgba(190, 150, 110, 0.9)';
        ctx.beginPath();
        ctx.arc(stubX, stubY, branch.baseWidth * 0.45, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
    }

    ctx.strokeStyle = `rgba(${(branch.color.r * dryness).toFixed(0)}, ${(branch.color.g * dryness).toFixed(0)}, ${(branch.color.b * dryness).toFixed(0)}, ${0.82 - branch.depth * 0.04})`;
    ctx.lineWidth = branch.baseWidth * (0.8 + 0.2 * (1 - progress));
    ctx.beginPath();
    ctx.moveTo(startX, startY);
    ctx.lineTo(endX, endY);
    ctx.stroke();

    branchSegments.push({ branch, x1: startX, y1: startY, x2: endX, y2: endY });

    if (branch.children.length === 0 && progress > 0.35) {
      drawLeaf(endX, endY, angle, branch, time);
      if (branch.depth >= CONFIG.fruitDepthStart) {
        drawFruit(endX, endY, branch, angle, time);
      }
    } else if (progress > 0.55) {
      branch.children.forEach((child) => drawBranch(child, time, angle, endX, endY));
    } else {
      drawBud(endX, endY, branch);
    }
  }

  function drawLeaf(x, y, angle, branch, time) {
    const seasonalShift = Math.sin(season + branch.seed * 3);
    const declineShift = lifeStage === 'decline' || lifeStage === 'seedFall' ? 35 : 0;
    const hue = clamp(110 + seasonalShift * 25 - declineShift, 35, 140);
    const lightness = 45 + Math.cos(season * 0.5 + branch.seed * 11) * 12;
    const length = 11 + (1 - branch.depth / CONFIG.maxDepth) * 6;
    const widthLeaf = 4 + (1 - branch.depth / CONFIG.maxDepth) * 2;
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle + Math.sin(time * 0.003 + branch.seed * 10) * 0.4);
    ctx.fillStyle = `hsla(${hue}, 65%, ${lightness}%, ${0.85 - 0.25 * (1 - growth)})`;
    ctx.beginPath();
    ctx.ellipse(0, 0, widthLeaf, length, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function drawBud(x, y, branch) {
    ctx.fillStyle = 'rgba(182, 255, 222, 0.45)';
    ctx.beginPath();
    ctx.arc(x, y, 3 + branch.depth * 0.2, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawFruit(x, y, branch, angle, time) {
    if (lifeStage !== 'fruit' && lifeStage !== 'decline' && lifeStage !== 'seedFall') {
      return;
    }

    if (!branch.fruits) {
      const count = seededRandom(branch.seed + 40) > 0.65 ? 2 : 1;
      branch.fruits = Array.from({ length: count }).map((_, idx) => ({
        offset: (idx === 0 ? -1 : 1) * (0.5 + seededRandom(branch.seed + idx) * 0.3),
        angleOffset: (idx === 0 ? -0.4 : 0.4) + (seededRandom(branch.seed + idx + 1) - 0.5) * 0.3,
        ripeness: 0.1 + Math.random() * 0.2,
        counted: false
      }));
    }

    branch.fruits.forEach((fruit) => {
      fruit.ripeness = clamp(fruit.ripeness + deltaMs * 0.00005 + hydration * 0.0001, 0, 1.2);
      if (fruit.ripeness > 0.98 && !fruit.counted) {
        fruitsRipened += 1;
        fruit.counted = true;
      }
      const swing = Math.sin(time * 0.003 + branch.seed * 14 + fruit.offset) * 4;
      const distance = 12 + fruit.ripeness * 6;
      const fruitX = x + Math.cos(angle + fruit.angleOffset) * distance * fruit.offset + swing;
      const fruitY = y + Math.sin(angle + fruit.angleOffset) * distance * 0.4 + 8;
      const radius = 4 + fruit.ripeness * 4;
      const hue = clamp(28 + fruit.ripeness * 60, 25, 90);
      ctx.fillStyle = `hsla(${hue}, 75%, ${55 - fruit.ripeness * 8}%, 0.9)`;
      ctx.beginPath();
      ctx.ellipse(fruitX, fruitY, radius * 0.8, radius, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = 'rgba(255, 255, 255, 0.35)';
      ctx.beginPath();
      ctx.arc(fruitX - radius * 0.2, fruitY - radius * 0.4, radius * 0.25, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  function pointerToCanvas(evt) {
    const rect = canvas.getBoundingClientRect();
    const x = ((evt.clientX - rect.left) / rect.width) * canvas.width;
    const y = ((evt.clientY - rect.top) / rect.height) * canvas.height;
    return { x, y };
  }

  function updatePointer(point) {
    sawState.x = point.x;
    sawState.y = point.y;
    if (sawState.active) {
      sawTrail.push({ x: point.x, y: point.y, life: 1 });
      if (sawTrail.length > 60) {
        sawTrail.shift();
      }
    }
  }

  function closestPoint(x1, y1, x2, y2, px, py) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const lenSq = dx * dx + dy * dy || 1;
    const t = clamp(((px - x1) * dx + (py - y1) * dy) / lenSq, 0, 1);
    return { x: x1 + dx * t, y: y1 + dy * t };
  }

  function spawnSawdust(point) {
    for (let i = 0; i < 12; i += 1) {
      sawdust.push({
        x: point.x,
        y: point.y,
        vx: (Math.random() - 0.5) * 0.35,
        vy: -0.2 - Math.random() * 0.3,
        life: 1
      });
    }
  }

  function performSawCut() {
    if (!branchSegments.length) return;
    const now = performance.now();
    if (now - sawState.lastCut < CONFIG.sawCooldown) return;

    let chosen = null;
    let bestDist = CONFIG.sawRadius;
    let chosenPoint = null;

    branchSegments.forEach((segment) => {
      if (segment.branch.depth === 0 || segment.branch.pruned) {
        return;
      }
      const point = closestPoint(segment.x1, segment.y1, segment.x2, segment.y2, sawState.x, sawState.y);
      const dist = Math.hypot(point.x - sawState.x, point.y - sawState.y);
      if (dist < bestDist) {
        bestDist = dist;
        chosen = segment;
        chosenPoint = point;
      }
    });

    if (chosen && chosenPoint) {
      chosen.branch.pruned = true;
      chosen.branch.regrowAt = now + 18000 + Math.random() * 14000;
      sawState.lastCut = now;
      spawnSawdust(chosenPoint);
      hintEl.textContent = 'Ветка убрана — жди, пока вырастет свежая почка.';
    }
  }

  function updateSawdust(delta) {
    sawdust = sawdust.filter((particle) => {
      particle.vy += 0.0004 * delta;
      particle.x += particle.vx * delta * 0.6;
      particle.y += particle.vy * delta * 0.6;
      particle.life -= delta * 0.0016;
      return particle.life > 0;
    });
  }

  function updateSawTrail(delta) {
    sawTrail = sawTrail
      .map((point) => ({ ...point, life: point.life - delta * 0.0028 }))
      .filter((point) => point.life > 0);
  }

  function drawSawOverlay() {
    sawdust.forEach((particle) => {
      ctx.fillStyle = `rgba(216, 198, 150, ${particle.life * 0.7})`;
      ctx.fillRect(particle.x, particle.y, 2, 2);
    });

    sawTrail.forEach((trailPoint) => {
      ctx.beginPath();
      ctx.fillStyle = `rgba(199, 255, 210, ${trailPoint.life * 0.5})`;
      ctx.arc(trailPoint.x, trailPoint.y, 6 * trailPoint.life, 0, Math.PI * 2);
      ctx.fill();
    });

    ctx.save();
    ctx.strokeStyle = sawState.active ? 'rgba(199, 255, 210, 0.9)' : 'rgba(199, 255, 210, 0.4)';
    ctx.lineWidth = sawState.active ? 2 : 1;
    ctx.beginPath();
    ctx.arc(sawState.x, sawState.y, CONFIG.sawRadius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function updateHud() {
    ageEl.textContent = `${(simulatedAge / 2 + growth * 10).toFixed(1)} лет`;
    moodEl.textContent = stageDescriptions[lifeStage] || 'Наблюдение';
    stageEl.textContent = `${Math.round(clamp(growth, 0, 1) * 100)}%`;
    fpsEl.textContent = `${Math.round(fps)} FPS`;
    if (sawState.active) {
      hintEl.textContent = 'Пила активна — отпусти кнопку, чтобы вновь наблюдать.';
    } else {
      hintEl.textContent = stageHints[lifeStage] || 'Полей дерево, чтобы ускорить рост.';
    }
  }

  function scheduleGrowthPulse() {
    const nextPulse = 3500 + Math.random() * 3500;
    setTimeout(() => {
      const maturityBonus =
        lifeStage === 'fruit' ? 1 : lifeStage === 'mature' ? 0.7 : lifeStage === 'seed' ? 1.2 : 0.5;
      targetGrowth = clamp(
        targetGrowth + CONFIG.pulseBoost * (0.4 + Math.random() * maturityBonus),
        0,
        1.08
      );
      simulatedAge += 0.4 + Math.random() * 0.7;
      scheduleGrowthPulse();
    }, nextPulse);
  }

  waterBtn.addEventListener('click', () => {
    hydration = Math.min(1.5, hydration + 0.9);
    targetGrowth = clamp(targetGrowth + 0.1, 0, 1.08);
    windTarget = (Math.random() - 0.5) * 0.5;
    hintEl.textContent = 'Дерево впитало влагу и тянется быстрее.';
  });

  canvas.addEventListener('pointerdown', (evt) => {
    if (evt.button !== 0) return;
    const point = pointerToCanvas(evt);
    sawState.active = true;
    updatePointer(point);
    performSawCut();
  });

  canvas.addEventListener('pointermove', (evt) => {
    const point = pointerToCanvas(evt);
    updatePointer(point);
    if (sawState.active) {
      performSawCut();
    }
  });

  window.addEventListener('pointerup', () => {
    sawState.active = false;
  });

  canvas.addEventListener('pointerleave', () => {
    sawState.active = false;
  });

  canvas.addEventListener('contextmenu', (evt) => evt.preventDefault());

  function loop(time) {
    deltaMs = time - lastTime || 16;
    deltaMs = Math.min(Math.max(deltaMs, 8), 120);
    lastTime = time;
    fps = fps * 0.9 + Math.min(120, 1000 / deltaMs) * 0.1;

    const lerpFactor = clamp(deltaMs * CONFIG.baseGrowthSpeed, 0, 0.04);
    growth += (targetGrowth - growth) * lerpFactor;
    growth = clamp(growth, 0, 1.08);

    hydration = Math.max(0, hydration - deltaMs * 0.00022);
    if (hydration > 0.001) {
      targetGrowth = clamp(targetGrowth + hydration * 0.0005, 0, 1.08);
    }

    wind += (windTarget - wind) * clamp(deltaMs * 0.0006, 0, 1);
    if (Math.random() < 0.002) {
      windTarget = (Math.random() - 0.5) * (0.4 + 0.3 * (1 - growth));
    }

    season += deltaMs * 0.000015;
    stageTimer += deltaMs;
    simulatedAge += deltaMs * 0.00005;

    updateLifeStage(deltaMs);
    updateFireflies(deltaMs);
    updateClouds(deltaMs);
    updateBirds(deltaMs);
    updateSaplings(deltaMs);
    updateSawdust(deltaMs);
    updateSawTrail(deltaMs);

    ctx.clearRect(0, 0, width, height);
    drawSky(time);
    drawMountains(time);
    drawCloudsLayer();
    drawFirefliesLayer();
    drawBirdsLayer(time);
    drawGround();
    drawSaplings(time);
    drawTree(time);
    drawSawOverlay();

    updateHud();

    requestAnimationFrame(loop);
  }

  scheduleGrowthPulse();
  requestAnimationFrame(loop);
})();
