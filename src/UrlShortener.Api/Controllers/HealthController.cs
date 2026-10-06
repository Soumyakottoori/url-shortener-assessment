using Microsoft.AspNetCore.Mvc;
using UrlShortener.Api.Services;

namespace UrlShortener.Api.Controllers;

[ApiController]
public sealed class HealthController(ILinkService linkService) : ControllerBase
{
    [HttpGet("/health")]
    public async Task<IActionResult> Get(CancellationToken cancellationToken)
    {
        if (!await linkService.CanConnectAsync(cancellationToken))
            return StatusCode(StatusCodes.Status503ServiceUnavailable, new ErrorResponse("Storage unavailable"));
        return Ok(new { status = "ok" });
    }
}