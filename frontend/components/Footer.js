import { project } from "@/lib/content";

export default function Footer() {
  return (
    <footer className="footer">
      <div className="container">
        <p className="muted">{project.title} · Mini Project</p>
        <p className="muted small">Developed by Bhuvana, Sathish, Lasya</p>
      </div>
    </footer>
  );
}
