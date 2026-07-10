function handler(event) {
    var request = event.request;
    var uri = request.uri;
    var lastSegment = uri.split('/').pop();

    // A real file (has an extension) passes through untouched.
    if (lastSegment.includes('.')) {
        return request;
    }

    // Directory WITH a trailing slash → serve its index.html.
    if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
        return request;
    }

    // Directory WITHOUT a trailing slash → 301 to the canonical slash URL,
    // so relative asset paths resolve inside the directory.
    return {
        statusCode: 301,
        statusDescription: 'Moved Permanently',
        headers: { 'location': { value: uri + '/' } }
    };
}
