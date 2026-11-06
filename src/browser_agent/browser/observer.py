"""Structured DOM observation across a page, open shadow roots, and all frames.

The observer adds short-lived data attributes to discovered elements. Those markers
allow :class:`LocatorResolver` to point back to the exact element without asking an
LLM to invent a CSS selector. A subsequent observation removes the previous markers,
so IDs are deliberately valid for one observation only.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import Frame, Page

from browser_agent.browser.locator_resolver import LocatorResolver
from browser_agent.schemas import BrowserObservation, ElementInfo, FrameInfo


_DISCOVERY_SCRIPT = r"""(args) => {
  const OBS_ATTR = 'data-ba-observation';
  const ELEMENT_ATTR = 'data-ba-element';
  const { observationId, frameId, maxElements } = args;

  const cleanRoot = (root) => {
    for (const element of root.querySelectorAll(`[${OBS_ATTR}], [${ELEMENT_ATTR}]`)) {
      element.removeAttribute(OBS_ATTR);
      element.removeAttribute(ELEMENT_ATTR);
    }
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) cleanRoot(host.shadowRoot);
    }
  };

  const roots = [];
  const collectRoots = (root) => {
    roots.push(root);
    for (const element of root.querySelectorAll('*')) {
      if (element.shadowRoot) collectRoots(element.shadowRoot);
    }
  };

  cleanRoot(document);
  collectRoots(document);

  const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const clipped = (value, max = 500) => {
    const text = normalize(value);
    return text.length > max ? `${text.slice(0, max - 1)}…` : (text || null);
  };
  const rootFor = (element) => {
    const root = element.getRootNode();
    return root && typeof root.querySelectorAll === 'function' ? root : document;
  };
  const labelledByText = (element) => {
    const ids = normalize(element.getAttribute('aria-labelledby')).split(' ').filter(Boolean);
    if (!ids.length) return '';
    const root = rootFor(element);
    return ids.map((id) => {
      const target = typeof root.getElementById === 'function'
        ? root.getElementById(id)
        : root.querySelector(`#${CSS.escape(id)}`);
      return target ? (target.innerText || target.textContent || '') : '';
    }).join(' ');
  };
  const associatedLabel = (element) => {
    if (element.labels && element.labels.length) {
      return normalize(Array.from(element.labels).map((label) => label.innerText || label.textContent || '').join(' '));
    }
    const wrappingLabel = element.closest && element.closest('label');
    return wrappingLabel ? normalize(wrappingLabel.innerText || wrappingLabel.textContent || '') : '';
  };
  const accessibleName = (element, role) => {
    const ariaLabel = element.getAttribute('aria-label');
    if (ariaLabel) return normalize(ariaLabel);
    const labelledBy = labelledByText(element);
    if (labelledBy) return labelledBy;
    const label = associatedLabel(element);
    if (label) return label;
    if (element.hasAttribute('alt') && element.getAttribute('alt')) return normalize(element.getAttribute('alt'));
    if (['button', 'submit', 'reset', 'image'].includes((element.getAttribute('type') || '').toLowerCase())) {
      const value = element.getAttribute('value');
      if (value) return normalize(value);
    }
    if (role === 'heading' || role === 'button' || role === 'link' || role === 'option' || role === 'menuitem'
        || element.hasAttribute('tabindex') || element.hasAttribute('onclick')) {
      const text = element.innerText || element.textContent || '';
      if (normalize(text)) return normalize(text);
    }
    if (element.getAttribute('title')) return normalize(element.getAttribute('title'));
    return '';
  };
  const inferRole = (element) => {
    const explicit = normalize(element.getAttribute('role')).split(' ')[0];
    if (explicit) return explicit;
    const tag = element.tagName.toLowerCase();
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'a' && element.hasAttribute('href')) return 'link';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return element.multiple ? 'listbox' : 'combobox';
    if (tag === 'input') {
      const type = (element.type || 'text').toLowerCase();
      if (type === 'hidden') return null;
      if (type === 'button' || type === 'submit' || type === 'reset' || type === 'image') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'range') return 'slider';
      if (type === 'number') return 'spinbutton';
      if (type === 'search') return 'searchbox';
      return 'textbox';
    }
    if (element.isContentEditable) return 'textbox';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'main') return 'main';
    if (tag === 'nav') return 'navigation';
    if (tag === 'form') return 'form';
    if (tag === 'dialog') return 'dialog';
    if (tag === 'img') return 'img';
    return null;
  };
  const isVisible = (element) => {
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
    return element.getClientRects().length > 0;
  };
  const isRelevant = (element, role) => {
    const tag = element.tagName.toLowerCase();
    if (tag === 'input' && (element.type || '').toLowerCase() === 'hidden') return false;
    if (role) return true;
    if (['button', 'a', 'input', 'select', 'textarea', 'summary', 'form', 'main', 'nav', 'dialog'].includes(tag)) return true;
    if (/^h[1-6]$/.test(tag)) return true;
    if (element.isContentEditable || element.hasAttribute('tabindex') || element.hasAttribute('onclick')) return true;
    return false;
  };
  const safeHref = (element) => {
    if (!element.href) return null;
    try {
      const url = new URL(element.href, document.baseURI);
      url.username = '';
      url.password = '';
      for (const key of Array.from(url.searchParams.keys())) {
        if (/(token|key|secret|password|passwd|auth|session|code|credential)/i.test(key)) {
          url.searchParams.set(key, '[REDACTED]');
        }
      }
      url.hash = url.hash.replace(
        /(token|key|secret|password|passwd|auth|session|code|credential)=([^&]+)/ig,
        '$1=%5BREDACTED%5D'
      );
      return url.href;
    } catch (_) {
      return null;
    }
  };

  const candidates = [];
  const limit = Math.max(1, Number(maxElements) || 500);
  let discoveredCount = 0;
  for (const root of roots) {
    for (const element of root.querySelectorAll('*')) {
      const role = inferRole(element);
      if (!isRelevant(element, role)) continue;
      discoveredCount += 1;
      if (candidates.length < limit) candidates.push({ element, role });
    }
  }

  const elements = [];
  for (const candidate of candidates) {
    const element = candidate.element;
    const role = candidate.role;
    const id = `${frameId}e${elements.length + 1}`;
    element.setAttribute(OBS_ATTR, observationId);
    element.setAttribute(ELEMENT_ATTR, id);
    const tag = element.tagName.toLowerCase();
    const inputType = tag === 'input' ? (element.type || 'text').toLowerCase() : null;
    const label = associatedLabel(element);
    const ariaLabel = element.getAttribute('aria-label');
    const rawText = element.innerText || element.textContent || '';
    const checked = typeof element.checked === 'boolean'
      ? element.checked
      : (element.getAttribute('aria-checked') === 'true' ? true
        : (element.getAttribute('aria-checked') === 'false' ? false : null));
    const selected = typeof element.selected === 'boolean' ? element.selected : null;
    elements.push({
      element_id: id,
      frame_id: frameId,
      tag,
      role: role || null,
      name: clipped(accessibleName(element, role)),
      label: clipped(label),
      placeholder: clipped(element.getAttribute('placeholder'), 300),
      input_type: inputType,
      text: clipped(rawText),
      href: safeHref(element),
      visible: isVisible(element),
      enabled: !element.disabled && element.getAttribute('aria-disabled') !== 'true',
      checked,
      id_attribute: clipped(element.id, 200),
      name_attribute: clipped(element.getAttribute('name'), 200),
      test_id: clipped(element.getAttribute('data-testid') || element.getAttribute('data-test'), 200),
      aria_label: clipped(ariaLabel, 300),
      autocomplete: clipped(element.getAttribute('autocomplete'), 100),
      required: Boolean(element.required || element.getAttribute('aria-required') === 'true'),
      read_only: Boolean(element.readOnly || element.getAttribute('aria-readonly') === 'true'),
      multiple: Boolean(element.multiple),
      selected
    });
  }

  const textParts = [];
  for (const root of roots) {
    let text = '';
    if (root === document) {
      text = document.body ? document.body.innerText : '';
    } else {
      text = root.innerText || root.textContent || '';
    }
    const normalized = normalize(text);
    if (normalized) textParts.push(normalized);
  }

  return {
    elements,
    visible_text: clipped(textParts.join(' '), 20000) || '',
    truncated: discoveredCount > limit,
    discovered_count: discoveredCount
  };
}"""

_CLEAN_MARKERS_SCRIPT = r"""() => {
  const clean = (root) => {
    for (const element of root.querySelectorAll('[data-ba-observation], [data-ba-element]')) {
      element.removeAttribute('data-ba-observation');
      element.removeAttribute('data-ba-element');
    }
    for (const host of root.querySelectorAll('*')) {
      if (host.shadowRoot) clean(host.shadowRoot);
    }
  };
  clean(document);
}"""


def _sanitize_url(raw_url: str) -> str:
    """Redact common secret-bearing URL parameters before model exposure."""
    try:
        parts = urlsplit(raw_url)
        hostname = parts.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = hostname
        if parts.port is not None:
            netloc = f"{netloc}:{parts.port}"
        sensitive = re.compile(
            r"(token|key|secret|password|passwd|auth|session|code|credential)",
            re.IGNORECASE,
        )
        query = [
            (key, "[REDACTED]" if sensitive.search(key) else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
        fragment = re.sub(
            r"(?i)(token|key|secret|password|passwd|auth|session|code|credential)=([^&]+)",
            r"\1=%5BREDACTED%5D",
            parts.fragment,
        )
        return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), fragment))
    except Exception:
        return raw_url


class BrowserObserver:
    """Inspect page structure and retain frame references for the latest snapshot."""

    def __init__(self, *, max_elements_per_frame: int = 500) -> None:
        if not isinstance(max_elements_per_frame, int) or max_elements_per_frame < 1:
            raise ValueError("max_elements_per_frame must be at least 1")
        self.max_elements_per_frame = max_elements_per_frame
        self._latest_observation: BrowserObservation | None = None
        self._latest_frames: dict[str, Frame] = {}
        self._previous_frames: list[Frame] = []

    @property
    def latest_observation(self) -> BrowserObservation | None:
        return self._latest_observation

    def locator_resolver(self) -> LocatorResolver:
        """Return a resolver scoped to the latest observation."""
        if self._latest_observation is None:
            raise RuntimeError("Call inspect(page) before requesting a locator resolver")
        return LocatorResolver(self._latest_observation, self._latest_frames)

    async def inspect(self, page: Page) -> BrowserObservation:
        """Inspect the main page and every attached frame, including nested frames.

        Open shadow roots are recursively traversed within each frame. Closed shadow
        roots cannot be inspected by browser automation and therefore remain outside
        the returned element inventory.
        """
        warnings: list[str] = []
        for old_frame in self._previous_frames:
            try:
                await old_frame.evaluate(_CLEAN_MARKERS_SCRIPT)
            except Exception:
                # The frame may have navigated or detached since the prior snapshot.
                pass

        frames = list(page.frames)
        frame_ids = {frame: f"f{index}" for index, frame in enumerate(frames)}
        frame_infos: list[FrameInfo] = []
        frame_refs: dict[str, Frame] = {}
        all_elements: list[ElementInfo] = []
        visible_text_parts: list[str] = []
        observation_id = f"obs_{uuid4().hex[:20]}"

        for frame in frames:
            frame_id = frame_ids[frame]
            parent = frame.parent_frame
            parent_frame_id = frame_ids.get(parent) if parent is not None else None
            depth = 0
            ancestor = parent
            while ancestor is not None:
                depth += 1
                ancestor = ancestor.parent_frame
            frame_refs[frame_id] = frame
            frame_infos.append(
                FrameInfo(
                    frame_id=frame_id,
                    parent_frame_id=parent_frame_id,
                    url=_sanitize_url(frame.url),
                    name=frame.name or None,
                    is_main_frame=frame == page.main_frame,
                    depth=depth,
                )
            )

            try:
                result: dict[str, Any] = await frame.evaluate(
                    _DISCOVERY_SCRIPT,
                    {
                        "observationId": observation_id,
                        "frameId": frame_id,
                        "maxElements": self.max_elements_per_frame,
                    },
                )
            except Exception as exc:
                warnings.append(
                    f"Could not inspect frame {frame_id} ({frame.url or 'unknown URL'}): "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            for item in result.get("elements", []):
                try:
                    all_elements.append(ElementInfo.model_validate(item))
                except Exception as exc:
                    warnings.append(
                        f"Skipped malformed element in frame {frame_id}: {type(exc).__name__}"
                    )
            frame_text = result.get("visible_text") or ""
            if frame_text:
                visible_text_parts.append(f"[Frame {frame_id}] {frame_text}")
            if result.get("truncated"):
                warnings.append(
                    f"Frame {frame_id} contained {result.get('discovered_count')} relevant "
                    f"elements; observation was capped at {self.max_elements_per_frame}."
                )

        try:
            title = await page.title()
        except Exception as exc:
            title = ""
            warnings.append(f"Could not read page title: {type(exc).__name__}: {exc}")

        observation = BrowserObservation(
            observation_id=observation_id,
            url=_sanitize_url(page.url),
            title=title,
            frames=frame_infos,
            elements=all_elements,
            visible_text=" ".join(visible_text_parts)[:20_000],
            warnings=warnings,
        )
        self._latest_observation = observation
        self._latest_frames = frame_refs
        self._previous_frames = frames
        return observation
