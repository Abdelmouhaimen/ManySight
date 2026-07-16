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
