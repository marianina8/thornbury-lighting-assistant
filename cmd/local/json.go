package main

import (
	"encoding/json"
	"io"
)

func jsonEncode(w io.Writer, v any) error {
	e := json.NewEncoder(w)
	e.SetIndent("", "  ")
	return e.Encode(v)
}
