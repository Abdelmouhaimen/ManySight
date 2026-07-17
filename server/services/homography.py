"""Homography estimation (normalized DLT) and projection. Pixel plane -> store map plane (meters)."""
import numpy as np


def _pt(p):
    if isinstance(p, dict):
        return [float(p["x"]), float(p["y"])]
    return [float(p[0]), float(p[1])]


def _normalize(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    d = np.sqrt(((pts - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2) / d if d > 1e-12 else 1.0
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1.0]])


def compute_homography(pairs: list[dict]) -> tuple[list[list[float]], float]:
    """pairs: [{"px": {x,y}, "map": {x,y}}, ...] with at least 4 entries.
    Returns (H as 3x3 nested list, mean reprojection error in map units)."""
    if len(pairs) < 4:
        raise ValueError("At least 4 point pairs are required for homography")
    src = np.array([_pt(p["px"]) for p in pairs], dtype=float)
    dst = np.array([_pt(p["map"]) for p in pairs], dtype=float)
    Ts, Td = _normalize(src), _normalize(dst)
    sh = np.c_[src, np.ones(len(src))] @ Ts.T
    dh = np.c_[dst, np.ones(len(dst))] @ Td.T
    A = []
    for (x, y, w), (u, v, ww) in zip(sh, dh):
        A.append([0, 0, 0, -ww * x, -ww * y, -ww * w, v * x, v * y, v * w])
        A.append([ww * x, ww * y, ww * w, 0, 0, 0, -u * x, -u * y, -u * w])
    _, _, Vt = np.linalg.svd(np.array(A))
    Hn = Vt[-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ Hn @ Ts
    if abs(H[2, 2]) < 1e-12:
        raise ValueError("Degenerate point configuration")
    H = H / H[2, 2]
    proj = np.array(project(H.tolist(), src.tolist()))
    err = float(np.sqrt(((proj - dst) ** 2).sum(axis=1)).mean())
    return H.tolist(), err


def project(H: list[list[float]], points: list) -> list[list[float]]:
    """Apply homography to a list of points ([x,y] or {x,y}). Returns [[x,y], ...]."""
    Hm = np.array(H, dtype=float)
    P = np.array([_pt(p) + [1.0] for p in points], dtype=float)
    out = P @ Hm.T
    w = out[:, 2:3]
    w[np.abs(w) < 1e-12] = 1e-12
    return (out[:, :2] / w).tolist()


def invert(H: list[list[float]]) -> list[list[float]]:
    """Invert a non-degenerate homography and normalize it to H[2][2] == 1."""
    try:
        inv = np.linalg.inv(np.array(H, dtype=float))
    except np.linalg.LinAlgError as exc:
        raise ValueError("Degenerate homography") from exc
    if abs(inv[2, 2]) < 1e-12:
        raise ValueError("Degenerate homography")
    return (inv / inv[2, 2]).tolist()


def point_in_polygon(x: float, y: float, poly: list[dict]) -> bool:
    """Ray-casting point-in-polygon. poly: [{x,y}, ...]."""
    inside = False
    n = len(poly)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]["x"], poly[i]["y"]
        xj, yj = poly[j]["x"], poly[j]["y"]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def polygon_area(poly: list[dict]) -> float:
    """Unsigned area of a simple polygon."""
    if len(poly) < 3:
        return 0.0
    total = 0.0
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        total += float(p["x"]) * float(q["y"]) - float(q["x"]) * float(p["y"])
    return abs(total) / 2.0


def polygon_box_overlap(poly: list[dict], bbox: list[float]) -> float:
    """Fraction of an axis-aligned bbox covered by a polygon, using polygon clipping."""
    if len(poly) < 3 or len(bbox) < 4 or bbox[2] <= 0 or bbox[3] <= 0:
        return 0.0
    x0, y0, w, h = map(float, bbox[:4])
    x1, y1 = x0 + w, y0 + h
    points = [{"x": float(p["x"]), "y": float(p["y"])} for p in poly]

    def clip(items, inside, intersect):
        if not items:
            return []
        result, previous = [], items[-1]
        for current in items:
            current_in, previous_in = inside(current), inside(previous)
            if current_in:
                if not previous_in:
                    result.append(intersect(previous, current))
                result.append(current)
            elif previous_in:
                result.append(intersect(previous, current))
            previous = current
        return result

    def vertical(a, b, x):
        dx = b["x"] - a["x"]
        t = (x - a["x"]) / dx if abs(dx) > 1e-12 else 0.0
        return {"x": x, "y": a["y"] + t * (b["y"] - a["y"])}

    def horizontal(a, b, y):
        dy = b["y"] - a["y"]
        t = (y - a["y"]) / dy if abs(dy) > 1e-12 else 0.0
        return {"x": a["x"] + t * (b["x"] - a["x"]), "y": y}

    points = clip(points, lambda p: p["x"] >= x0, lambda a, b: vertical(a, b, x0))
    points = clip(points, lambda p: p["x"] <= x1, lambda a, b: vertical(a, b, x1))
    points = clip(points, lambda p: p["y"] >= y0, lambda a, b: horizontal(a, b, y0))
    points = clip(points, lambda p: p["y"] <= y1, lambda a, b: horizontal(a, b, y1))
    return min(1.0, polygon_area(points) / (w * h))
