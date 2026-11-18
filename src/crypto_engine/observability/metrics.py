"""Thread-safe Prometheus-compatible metrics registry (stdlib only).

Advisory-only observability: no live trading, no automatic promotion,
no order execution. Label values are redacted for secret-like content
before exposition (see ``_redact_label_value``); callers should still
avoid placing sensitive account information in metric values.

Max series: 10,000. When the limit is reached the oldest series (by
insertion order) is evicted before registering a new one.
"""

from __future__ import annotations

import re
import threading
from typing import Any

_SECRET_LABEL_RE = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|api[_-]?key\s*=\s*\S+",
    re.IGNORECASE,
)


def _redact_label_value(value: str) -> str:
    """Redact secret-like label values before Prometheus exposition.

    Returns ``"REDACTED"`` when ``value`` matches a known secret pattern
    (OpenAI key, Bearer token, API key assignment) or contains ``sk-`` /
    ``Bearer `` substrings; otherwise returns the value unchanged.
    """
    if "sk-" in value or "Bearer " in value:
        return "REDACTED"
    if _SECRET_LABEL_RE.search(value):
        return "REDACTED"
    return value


class Counter:
    """Monotonically increasing counter.

    Attributes:
        name: Metric name.
        labels: Frozen label mapping attached to this series.
        help_text: Human-readable description.
        _value: Current counter value.
        _lock: Serializes increments and reads.
    """

    __slots__ = ("_lock", "_type", "_value", "help_text", "labels", "name")

    def __init__(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        self.name = name
        self.labels = dict(labels or {})
        self.help_text = help_text
        self._value = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1) -> None:
        """Atomically increase the counter by ``amount``."""
        if amount < 0:
            raise ValueError("Counter cannot be decreased")
        with self._lock:
            self._value += amount

    def value(self) -> float:
        """Return the current counter value."""
        with self._lock:
            return self._value


class Gauge:
    """Gauge that can go up and down.

    Attributes:
        name: Metric name.
        labels: Frozen label mapping attached to this series.
        help_text: Human-readable description.
        _value: Current gauge value.
        _lock: Serializes sets/increments/decrements and reads.
    """

    __slots__ = ("_lock", "_type", "_value", "help_text", "labels", "name")

    def __init__(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> None:
        self.name = name
        self.labels = dict(labels or {})
        self.help_text = help_text
        self._value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        """Set the gauge to ``value``."""
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1) -> None:
        """Atomically increase the gauge by ``amount``."""
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1) -> None:
        """Atomically decrease the gauge by ``amount``."""
        with self._lock:
            self._value -= amount

    def get(self) -> float:
        """Return the current gauge value."""
        with self._lock:
            return self._value


class Histogram:
    """Histogram tracking count, sum, min, max, and bucket counts.

    Attributes:
        name: Metric name.
        labels: Frozen label mapping attached to this series.
        help_text: Human-readable description.
        buckets: Upper bounds of each bucket.
        _count: Total number of observations.
        _sum: Sum of all observations.
        _min: Minimum observation (None if none observed).
        _max: Maximum observation (None if none observed).
        _bucket_counts: Count per bucket, indexed like ``buckets``.
        _lock: Serializes observes and snapshots.
    """

    __slots__ = (
        "_bucket_counts",
        "_count",
        "_lock",
        "_max",
        "_min",
        "_sum",
        "_type",
        "buckets",
        "help_text",
        "labels",
        "name",
    )

    def __init__(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        help_text: str = "",
        buckets: tuple[float, ...] | None = None,
    ) -> None:
        self.name = name
        self.labels = dict(labels or {})
        self.help_text = help_text
        self.buckets = tuple(buckets) if buckets is not None else (
            0.005,
            0.01,
            0.025,
            0.05,
            0.1,
            0.25,
            0.5,
            1.0,
            2.5,
            5.0,
            10.0,
        )
        self._count = 0
        self._sum = 0.0
        self._min: float | None = None
        self._max: float | None = None
        self._bucket_counts: list[int] = [0] * len(self.buckets)
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        """Record a single observation."""
        with self._lock:
            self._count += 1
            self._sum += value
            if self._min is None or value < self._min:
                self._min = value
            if self._max is None or value > self._max:
                self._max = value
            for index, bound in enumerate(self.buckets):
                if value <= bound:
                    self._bucket_counts[index] += 1
                    break

    def snapshot(self) -> dict[str, Any]:
        """Return a copy of the current histogram state."""
        with self._lock:
            return {
                "count": self._count,
                "sum": self._sum,
                "min": self._min,
                "max": self._max,
                "buckets": list(self.buckets),
                "bucket_counts": list(self._bucket_counts),
            }


def _escape(value: str) -> str:
    """Escape a label value for Prometheus text exposition format."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _label_string(labels: dict[str, str]) -> str:
    """Render ``labels`` as a Prometheus label set (``{k="v",...}``)."""
    if not labels:
        return ""
    items = ','.join(
        f'{key}="{_escape(_redact_label_value(str(val)))}"'
        for key, val in sorted(labels.items())
    )
    return "{" + items + "}"


class MetricsRegistry:
    """Thread-safe registry of metric series with bounded storage.

    Singleton: use :meth:`get_instance` to obtain the shared registry.

    Attributes:
        max_series: Maximum number of series before oldest eviction.
        _series: Insertion-ordered mapping of series key to metric object.
        _lock: Serializes registration, collection, and reset.
    """

    _instance: MetricsRegistry | None = None
    _instance_lock = threading.Lock()

    def __init__(self, max_series: int = 10_000) -> None:
        self.max_series = max_series
        self._max_series = max_series
        self._series: dict[str, Any] = {}
        self._lock = threading.Lock()
    @classmethod
    def get_instance(cls) -> MetricsRegistry:
        """Return the singleton registry instance, creating it if needed."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _series_key(self, name: str, labels: dict[str, str] | None) -> str:
        """Build a deterministic series key from name and labels."""
        label_parts = ",".join(
            f'{key}={val!s}' for key, val in sorted(labels.items())
        )
        return f"{name}{{{label_parts}}}" if label_parts else name

    def register(
        self,
        name: str,
        type: str,
        labels: dict[str, str] | None = None,
        help_text: str = "",
    ) -> Any:
        """Create or return the metric series for ``name`` + ``labels``.

        Args:
            name: Metric name.
            type: One of ``"counter"``, ``"gauge"``, ``"histogram"``.
            labels: Optional label mapping.
            help_text: Human-readable description.

        Returns:
            The existing or newly created metric instance.

        Raises:
            ValueError: If ``type`` is unknown or a series with the same key
                exists under a different type.
        """
        labels = dict(labels or {})
        key = self._series_key(name, labels)
        metric_type = type.lower()

        with self._lock:
            existing = self._series.get(key)
            if existing is not None:
                if getattr(existing, "_type", None) != metric_type:
                    raise ValueError(
                        f"Series {key!r} already registered as type "
                        f"{existing._type!r}, not {metric_type!r}"
                    )
                return existing

            if len(self._series) >= self.max_series:
                oldest_key = next(iter(self._series))
                del self._series[oldest_key]

            if metric_type == "counter":
                metric: Any = Counter(name, labels, help_text)
            elif metric_type == "gauge":
                metric = Gauge(name, labels, help_text)
            elif metric_type == "histogram":
                metric = Histogram(name, labels, help_text)
            else:
                raise ValueError(f"Unknown metric type: {type!r}")

            metric._type = metric_type  # type: ignore[attr-defined]
            self._series[key] = metric
            return metric

    def counter(self, name: str, **labels: str) -> Counter:
        """Get or register a :class:`Counter` series with the given labels."""
        return self.register(name, "counter", labels, "")

    def gauge(self, name: str, **labels: str) -> Gauge:
        """Get or register a :class:`Gauge` series with the given labels."""
        return self.register(name, "gauge", labels, "")

    def histogram(
        self,
        name: str,
        buckets: tuple[float, ...] | None = None,
        **labels: str,
    ) -> Histogram:
        """Get or register a :class:`Histogram` series with the given labels."""
        metric = self.register(name, "histogram", labels, "")
        if buckets is not None:
            metric.buckets = tuple(buckets)
            metric._bucket_counts = [0] * len(metric.buckets)  # type: ignore[attr-defined]
        return metric

    def collect_all(self) -> dict[str, list[dict[str, Any]]]:
        """Return all metrics as a dict of name -> list of series samples."""
        with self._lock:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for key, metric in self._series.items():
                entry = {
                    "key": key,
                    "name": metric.name,
                    "type": getattr(metric, "_type", "unknown"),
                    "labels": dict(metric.labels),
                    "help_text": getattr(metric, "help_text", ""),
                }
                if isinstance(metric, Counter):
                    entry["value"] = metric.value()
                elif isinstance(metric, Gauge):
                    entry["value"] = metric.get()
                elif isinstance(metric, Histogram):
                    entry["snapshot"] = metric.snapshot()
                grouped.setdefault(metric.name, []).append(entry)
            return grouped

    def render_prometheus(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            items = list(self._series.items())
        for _key, metric in items:
            metric_type = getattr(metric, "_type", "unknown")
            help_text = getattr(metric, "help_text", "")
            lines.append(f"# HELP {metric.name} {help_text}")
            lines.append(f"# TYPE {metric.name} {metric_type}")
            labels = metric.labels
            label_str = _label_string(labels)
            if isinstance(metric, Counter):
                lines.append(f"{metric.name}{label_str} {metric.value()}")
            elif isinstance(metric, Gauge):
                lines.append(f"{metric.name}{label_str} {metric.get()}")
            elif isinstance(metric, Histogram):
                snapshot = metric.snapshot()
                for index, bound in enumerate(snapshot["buckets"]):
                    le = str(bound)
                    count = sum(snapshot["bucket_counts"][: index + 1])
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = le
                    bucket_label_str = _label_string(bucket_labels)
                    lines.append(
                        f'{metric.name}_bucket{bucket_label_str} {count}'
                    )
                bucket_labels = dict(labels)
                bucket_labels["le"] = "+Inf"
                bucket_label_str = _label_string(bucket_labels)
                lines.append(
                    f'{metric.name}_bucket{bucket_label_str} {snapshot["count"]}'
                )
                lines.append(
                    f'{metric.name}_sum{label_str} {snapshot["sum"]}'
                )
                lines.append(
                    f'{metric.name}_count{label_str} {snapshot["count"]}'
                )
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        """Remove all series (primarily for testing)."""
        with self._lock:
            self._series.clear()


def get_instance() -> MetricsRegistry:
    """Return the singleton metrics registry instance."""
    return MetricsRegistry.get_instance()


def render_prometheus() -> str:
    """Render the singleton registry in Prometheus text exposition format."""
    return MetricsRegistry.get_instance().render_prometheus()


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "get_instance",
    "render_prometheus",
]

