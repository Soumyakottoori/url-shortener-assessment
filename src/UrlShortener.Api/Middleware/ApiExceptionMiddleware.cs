using Microsoft.EntityFrameworkCore;
using UrlShortener.Api;
using UrlShortener.Api.Services;

namespace UrlShortener.Api.Middleware;

public sealed class ApiExceptionMiddleware(RequestDelegate next, ILogger<ApiExceptionMiddleware> logger)
{
    public async Task InvokeAsync(HttpContext context)
    {
        try
        {
            await next(context);
        }
        catch (ApiException exception)
        {
            await WriteErrorAsync(context, exception.StatusCode, exception.Message);
        }
        catch (DbUpdateException)
        {
            await WriteErrorAsync(context, StatusCodes.Status503ServiceUnavailable, "Storage unavailable");
        }
        catch (Exception exception)
        {
            logger.LogError(exception, "Unhandled API error");
            await WriteErrorAsync(context, StatusCodes.Status500InternalServerError, "Internal server error");
        }
    }

    private static async Task WriteErrorAsync(HttpContext context, int statusCode, string message)
    {
        if (context.Response.HasStarted)
            throw new InvalidOperationException("The response has already started.");
        context.Response.Clear();
        context.Response.StatusCode = statusCode;
        await context.Response.WriteAsJsonAsync(new ErrorResponse(message));
    }
}