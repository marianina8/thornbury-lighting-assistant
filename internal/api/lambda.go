package api

import (
	"context"
	"encoding/base64"
	"net/http"
	"net/http/httptest"
	"strings"

	"github.com/aws/aws-lambda-go/events"
)

// LambdaAdapter serves an API Gateway HTTP API (payload v2) event through an
// ordinary http.Handler, so the same handler runs in Lambda, in cmd/local and
// in tests.
func LambdaAdapter(h http.Handler) func(context.Context, events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
	return func(ctx context.Context, ev events.APIGatewayV2HTTPRequest) (events.APIGatewayV2HTTPResponse, error) {
		body := ev.Body
		if ev.IsBase64Encoded {
			b, err := base64.StdEncoding.DecodeString(body)
			if err != nil {
				return events.APIGatewayV2HTTPResponse{StatusCode: 400, Body: `{"error":"bad body encoding","code":"bad_request"}`}, nil
			}
			body = string(b)
		}
		path := ev.RawPath
		if path == "" {
			path = ev.RequestContext.HTTP.Path
		}
		url := path
		if ev.RawQueryString != "" {
			url += "?" + ev.RawQueryString
		}
		req, err := http.NewRequestWithContext(ctx, ev.RequestContext.HTTP.Method, url, strings.NewReader(body))
		if err != nil {
			return events.APIGatewayV2HTTPResponse{StatusCode: 400}, nil
		}
		for k, v := range ev.Headers {
			req.Header.Set(k, v)
		}
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		headers := map[string]string{}
		for k, v := range rec.Header() {
			headers[k] = strings.Join(v, ",")
		}
		return events.APIGatewayV2HTTPResponse{StatusCode: rec.Code, Headers: headers, Body: rec.Body.String()}, nil
	}
}
