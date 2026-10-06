using Microsoft.AspNetCore.Mvc;
using UrlShortener.Api.Services;

namespace UrlShortener.Api.Controllers;

[ApiController]
public sealed class RedirectController(ILinkService linkService) : ControllerBase
{
    [HttpGet("/r/{code}")]
    public async Task<IActionResult> RedirectToDestination(string code, CancellationToken cancellationToken)
    {
        var destination = await linkService.ResolveAsync(code, cancellationToken);
        return Redirect(destination);
    }
}