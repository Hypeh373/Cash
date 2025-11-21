#!/usr/bin/env python3
import random

template_top = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Люмин ОС — Симулятор ПК</title>
<meta name="description" content="Интерактивный симулятор ПК с элементами Windows 11, мини-играми и приложениями в одном файле" />
<style>
:root {
  --glass: rgba(15, 23, 42, 0.65);
  --glass-strong: rgba(15, 23, 42, 0.85);
  --stroke: rgba(255, 255, 255, 0.12);
  --soft-stroke: rgba(255, 255, 255, 0.06);
  --glow: rgba(56, 189, 248, 0.45);
  --text: #f8fbff;
  --muted: rgba(241, 245, 249, 0.7);
  --accent-blue: #38bdf8;
  --accent-pink: #f472b6;
  --accent-amber: #fbbf24;
  --accent-lime: #a3e635;
  --accent-purple: #c084fc;
  --taskbar-height: 66px;
  --danger: #fb7185;
  --success: #34d399;
  font-family: "Inter", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: radial-gradient(circle at top left, #1e3a8a, #020617 42%);
  color: var(--text);
  min-height: 100vh;
  overflow: hidden;
  user-select: none;
}

.hidden {
  opacity: 0;
  pointer-events: none;
}

.boot-screen {
  position: fixed;
  inset: 0;
  background: linear-gradient(135deg, #020617, #0f172a 45%, #1d4ed8);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24px;
  z-index: 999;
}

.boot-logo {
  font-size: 72px;
  font-weight: 600;
  letter-spacing: 0.2rem;
}

.boot-progress {
  width: min(420px, 70vw);
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.boot-progress span {
  display: block;
  width: 0%;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent-purple), var(--accent-blue));
  transition: width 0.6s ease;
}

.desktop {
  position: relative;
  inset: 0;
  height: 100vh;
  width: 100%;
  overflow: hidden;
}

.wallpaper {
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 20% 20%, rgba(56, 189, 248, 0.45), transparent 45%),
              radial-gradient(circle at 80% 30%, rgba(244, 114, 182, 0.45), transparent 40%),
              linear-gradient(135deg, #020617, #0f172a 60%, #1e1b4b);
  filter: saturate(1.2) brightness(1.05);
  z-index: 0;
}

.wallpaper::after {
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(circle, rgba(255, 255, 255, 0.08) 10%, transparent 55%);
  opacity: 0.4;
}

.desktop-grid {
  position: absolute;
  inset: 32px;
  bottom: calc(var(--taskbar-height) + 24px);
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
  gap: 18px;
  align-content: flex-start;
  z-index: 2;
}

.desktop-icon {
  background: rgba(15, 23, 42, 0.4);
  border-radius: 18px;
  padding: 16px 12px;
  border: 1px solid transparent;
  text-align: center;
  transition: all 0.2s ease;
  cursor: default;
}

.desktop-icon.active,
.desktop-icon:focus,
.desktop-icon:hover {
  border-color: rgba(255, 255, 255, 0.3);
  background: rgba(15, 23, 42, 0.55);
  box-shadow: 0 15px 30px rgba(2, 6, 23, 0.45);
}

.desktop-icon .glyph {
  font-size: 36px;
  display: block;
  margin-bottom: 10px;
}

.desktop-icon span {
  font-size: 14px;
  color: var(--text);
}

.window-layer {
  position: absolute;
  inset: 0;
  padding: 48px;
  z-index: 3;
  pointer-events: none;
}

.window {
  position: absolute;
  top: 120px;
  left: 280px;
  width: 640px;
  height: 420px;
  border-radius: 26px;
  background: rgba(15, 23, 42, 0.85);
  backdrop-filter: blur(30px);
  border: 1px solid var(--soft-stroke);
  box-shadow: 0 30px 70px rgba(2, 6, 23, 0.6);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  pointer-events: auto;
  transition: all 0.2s ease;
}

.window.maximized {
  top: 32px;
  left: 32px;
  width: calc(100% - 64px);
  height: calc(100% - var(--taskbar-height) - 48px);
  border-radius: 0;
}

.window.minimized {
  transform: scale(0.8) translateY(30%);
  opacity: 0;
  pointer-events: none;
}

.window-titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: rgba(15, 23, 42, 0.65);
  border-bottom: 1px solid var(--soft-stroke);
  cursor: grab;
}

.window-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.window-controls button {
  width: 36px;
  height: 32px;
  border: none;
  background: transparent;
  color: var(--text);
  font-size: 16px;
  border-radius: 12px;
  cursor: pointer;
  transition: background 0.2s ease;
}

.window-controls button:hover {
  background: rgba(255, 255, 255, 0.08);
}

.window-controls button[data-action="close"]:hover {
  background: rgba(248, 113, 113, 0.35);
}

.window-body {
  flex: 1;
  overflow: auto;
  padding: 18px 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.taskbar {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: 16px;
  width: min(940px, 94vw);
  height: var(--taskbar-height);
  border-radius: 28px;
  background: rgba(15, 23, 42, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.08);
  backdrop-filter: blur(22px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  gap: 18px;
  z-index: 10;
}

.taskbar button {
  border: none;
  background: transparent;
  color: var(--text);
  width: 46px;
  height: 46px;
  border-radius: 16px;
  font-size: 20px;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.2s ease;
}

.taskbar button:hover,
.taskbar button.active {
  background: rgba(148, 163, 184, 0.2);
}

.taskbar-clock {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
  text-align: right;
}

.taskbar-clock span {
  font-size: 15px;
}

.taskbar-clock small {
  font-size: 12px;
  color: var(--muted);
}

#taskbarPinned {
  display: flex;
  align-items: center;
  gap: 12px;
}

#taskbarPinned button {
  width: 44px;
  height: 44px;
  font-size: 20px;
}

.start-menu,
.widget-panel,
.notification-center,
.context-menu {
  position: absolute;
  background: rgba(15, 23, 42, 0.82);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 28px;
  backdrop-filter: blur(30px);
  box-shadow: 0 20px 60px rgba(2, 6, 23, 0.55);
  transition: opacity 0.2s ease, transform 0.2s ease;
  z-index: 12;
}

.start-menu {
  width: min(620px, 90vw);
  padding: 26px;
  left: 50%;
  transform: translate(-50%, 20px);
  bottom: calc(var(--taskbar-height) + 32px);
}

.start-menu.hidden {
  opacity: 0;
  pointer-events: none;
  transform: translate(-50%, 40px);
}

.start-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 20px;
}

.start-profile {
  display: flex;
  align-items: center;
  gap: 14px;
}

.start-profile .avatar {
  width: 56px;
  height: 56px;
  border-radius: 18px;
  background: linear-gradient(135deg, var(--accent-purple), var(--accent-blue));
}

.start-header input {
  flex: 1;
  height: 48px;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(15, 23, 42, 0.65);
  color: var(--text);
  padding: 0 18px;
  font-size: 16px;
}

.start-section {
  margin-bottom: 18px;
}

.section-title {
  font-size: 15px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 12px;
}

.start-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 14px;
}

.pinned-app {
  background: rgba(255, 255, 255, 0.05);
  border-radius: 16px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border: 1px solid transparent;
  cursor: pointer;
}

.pinned-app:hover {
  border-color: rgba(255, 255, 255, 0.2);
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.1);
}

.pinned-app span {
  font-size: 28px;
}

.start-recommended {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.recommended-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 16px;
  padding: 12px 16px;
  cursor: pointer;
  border: 1px solid transparent;
}

.recommended-item:hover {
  border-color: rgba(255, 255, 255, 0.2);
}

.widget-panel {
  width: min(420px, 92vw);
  right: 32px;
  top: 32px;
  padding: 24px;
  transform: translateY(-20px);
}

.widget-panel.hidden {
  opacity: 0;
  pointer-events: none;
  transform: translateY(-40px);
}

.widget-panel-header {
  display: flex;
  justify-п...PY
