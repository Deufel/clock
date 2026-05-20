package web

import "fmt"

// FmtElapsed renders a duration in either "MM:SS.cc" or "Hh MMm SSs".
// Tracking shows centiseconds because at 60fps the user can see them move.
func FmtElapsed(secs float64) string {
	if secs >= 3600 {
		s := int(secs)
		hh := s / 3600
		rem := s - hh*3600
		mm := rem / 60
		ss := rem - mm*60
		cs := int(secs*100) % 100
		return fmt.Sprintf("%dh %02dm %02d.%02ds", hh, mm, ss, cs)
	}
	s := int(secs)
	mm := s / 60
	ss := secs - float64(mm*60)
	return fmt.Sprintf("%02d:%05.2f", mm, ss)
}

// FmtDuration renders a coarser duration for admin stats.
func FmtDuration(secs float64) string {
	switch {
	case secs < 60:
		return fmt.Sprintf("%.0fs", secs)
	case secs < 3600:
		s := int(secs)
		return fmt.Sprintf("%dm %ds", s/60, s%60)
	default:
		s := int(secs)
		return fmt.Sprintf("%dh %dm", s/3600, (s%3600)/60)
	}
}
