import { Link } from "@tanstack/react-router";
import { Shell } from "../shell/Shell";

export function NotFound() {
  return (
    <Shell section="item" title="找不到頁面">
      <div className="placeholder">
        <div className="card">
          <h1>找不到這個頁面</h1>
          <p>網址可能有誤，或頁面已經移走。</p>
          <Link className="btn-legacy" to="/">回到串流</Link>
        </div>
      </div>
    </Shell>
  );
}
