from devito import VectorTimeFunction, TimeFunction, Function, NODE
from devito.tools import memoized_meth
from examples.seismic import PointSource
from examples.seismic.multiparameter.viscoacoustic.operators import (
    ForwardOperator, AdjointOperator, NoAdjointOperator, GradientOperator, BornOperator
)


class ViscoacousticWaveSolver(object):
    """
    Solver object that provides operators for seismic inversion problems
    and encapsulates the time and space discretization for a given problem
    setup.

    Parameters
    ----------
    model : Model
        Physical model with domain parameters.
    geometry : AcquisitionGeometry
        Geometry object that contains the source (SparseTimeFunction) and
        receivers (SparseTimeFunction) and their position.
    space_order : int, optional
        Order of the spatial stencil discretisation. Defaults to 4.
    kernel : selects a visco-acoustic equation from the options below:
                'sls' (Standard Linear Solid) :
                1st order - Blanch and Symes (1995) / Dutta and Schuster (2014)
                viscoacoustic equation
                2nd order - Bai et al. (2014) viscoacoustic equation
                'ren' - Ren et al. (2014) viscoacoustic equation
                'deng_mcmechan' - Deng and McMechan (2007) viscoacoustic equation
                Defaults to 'sls' 2nd order.
    """
    def __init__(self, model, geometry, space_order=4, kernel='sls', time_order=2,
                 **kwargs):
        self.model = model
        self.model._initialize_bcs(bcs="mask")
        self.geometry = geometry

        self.space_order = space_order
        self.kernel = kernel
        self.time_order = time_order
        self._kwargs = kwargs

    @property
    def dt(self):
        return self.model.critical_dt

    @memoized_meth
    def op_fwd(self, save=None):
        """Cached operator for forward runs with buffered wavefield"""
        return ForwardOperator(self.model, save=save, geometry=self.geometry,
                               space_order=self.space_order, kernel=self.kernel,
                               time_order=self.time_order, **self._kwargs)

    @memoized_meth
    def op_adj(self):
        """Cached operator for adjoint runs"""
        return AdjointOperator(self.model, save=None, geometry=self.geometry,
                               space_order=self.space_order, kernel=self.kernel,
                               time_order=self.time_order, **self._kwargs)

    @memoized_meth
    def op_noadj(self):
        """Cached operator for adjoint runs"""
        return NoAdjointOperator(self.model, save=None, geometry=self.geometry,
                                 space_order=self.space_order, kernel=self.kernel,
                                 time_order=self.time_order, **self._kwargs)

    @memoized_meth
    def op_grad(self, save=True):
        """Cached operator for gradient runs"""
        return GradientOperator(self.model, save=save, geometry=self.geometry,
                                space_order=self.space_order, kernel=self.kernel,
                                time_order=self.time_order, **self._kwargs)

    @memoized_meth
    def op_born(self):
        """Cached operator for born runs"""
        return BornOperator(self.model, save=None, geometry=self.geometry,
                            space_order=self.space_order, kernel=self.kernel,
                            time_order=self.time_order, **self._kwargs)

    def forward(self, src=None, rec=None, v=None, r=None, p=None, model=None,
                save=None, **kwargs):
        """
        Forward modelling function that creates the necessary
        data objects for running a forward modelling operator.

        Parameters
        ----------
        src : SparseTimeFunction or array_like, optional
            Time series data for the injected source term.
        rec : SparseTimeFunction or array_like, optional
            The interpolated receiver data.
        v : VectorTimeFunction, optional
            The computed particle velocity.
        r : TimeFunction, optional
            The computed attenuation memory variable.
        p : TimeFunction, optional
            Stores the computed wavefield.
        model : Model, optional
            Object containing the physical parameters.
        qp : Function, optional
            The P-wave quality factor.
        b : Function, optional
            The time-constant inverse density.
        vp : Function or float, optional
            The time-constant velocity.
        save : bool, optional
            Whether or not to save the entire (unrolled) wavefield.

        Returns
        -------
        Receiver, wavefield and performance summary
        """
        # Source term is read-only, so re-use the default
        src = src or self.geometry.src

        # Create a new receiver object to store the result
        rec = rec or self.geometry.rec

        # Create all the fields v, p, r
        save_t = src.nt if save else None

        if self.time_order == 1:
            v = v or VectorTimeFunction(name="v", grid=self.model.grid, save=save_t,
                                        time_order=self.time_order,
                                        space_order=self.space_order)
            kwargs.update({k.name: k for k in v})

        # Create the forward wavefield if not provided
        p = p or TimeFunction(name="p", grid=self.model.grid, save=save_t,
                              time_order=self.time_order, space_order=self.space_order,
                              staggered=NODE)

        # Memory variable:
        r = r or TimeFunction(name="r", grid=self.model.grid, save=save_t,
                              time_order=self.time_order, space_order=self.space_order,
                              staggered=NODE)

        model = model or self.model
        # Pick vp and physical parameters from model unless explicitly provided
        kwargs.update(model.physical_params(**kwargs))

        if self.kernel == 'sls':
            # Execute operator and return wavefield and receiver data
            # With Memory variable
            summary = self.op_fwd(save).apply(src=src, rec=rec, r=r, p=p,
                                              dt=kwargs.pop('dt', self.dt), **kwargs)
            if self.time_order == 1:
                return rec, p, v, r, summary
            else:
                return rec, p, r, summary

        elif self.kernel == 'acoustic':
            kwargs.pop('qp', None)
            summary = self.op_fwd(save).apply(src=src, rec=rec, p=p,
                                              dt=kwargs.pop('dt', self.dt), **kwargs)
        else:
            # Execute operator and return wavefield and receiver data
            # Without Memory variable
            summary = self.op_fwd(save).apply(src=src, rec=rec, p=p,
                                              dt=kwargs.pop('dt', self.dt), **kwargs)

        if self.time_order == 1:
            return rec, p, v, summary
        else:
            return rec, p, summary

    def adjoint(self, rec, srca=None, va=None, pa=None, r=None, model=None, **kwargs):
        """
        Adjoint modelling function that creates the necessary
        data objects for running an adjoint modelling operator.

        Parameters
        ----------
        rec : SparseTimeFunction or array-like
            The receiver data. Please note that
            these act as the source term in the adjoint run.
        srca : SparseTimeFunction or array-like
            The resulting data for the interpolated at the
            original source location.
        va : VectorTimeFunction, optional
            The computed particle velocity.
        pa : TimeFunction, optional
            Stores the computed wavefield.
        r : TimeFunction, optional
            The computed attenuation memory variable.
        model : Model, optional
            Object containing the physical parameters.
        vp : Function or float, optional
            The time-constant velocity.
        qp : Function, optional
            The P-wave quality factor.
        b : Function, optional
            The time-constant inverse density.

        Returns
        -------
        Adjoint source, wavefield and performance summary.
        """
        # Create a new adjoint source and receiver symbol
        srca = srca or PointSource(name='srca', grid=self.model.grid,
                                   time_range=self.geometry.time_axis,
                                   coordinates=self.geometry.src_positions)

        if self.time_order == 1:
            va = va or VectorTimeFunction(name="va", grid=self.model.grid,
                                          time_order=self.time_order,
                                          space_order=self.space_order)
            kwargs.update({k.name: k for k in va})
            kwargs['time_m'] = 0

        pa = pa or TimeFunction(name="pa", grid=self.model.grid,
                                time_order=self.time_order, space_order=self.space_order,
                                staggered=NODE)

        # Memory variable:
        r = r or TimeFunction(name="r", grid=self.model.grid, time_order=self.time_order,
                              space_order=self.space_order, staggered=NODE)

        model = model or self.model
        # Pick vp and physical parameters from model unless explicitly provided
        kwargs.update(model.physical_params(**kwargs))

        # Execute operator and return wavefield and receiver data
        if self.kernel == 'sls':
            # Execute operator and return wavefield and receiver data
            # With Memory variable
            summary = self.op_adj().apply(src=srca, rec=rec, pa=pa, r=r,
                                          dt=kwargs.pop('dt', self.dt), **kwargs)
        elif self.kernel == 'acoustic':
            kwargs.pop('qp', None)
            summary = self.op_adj().apply(src=srca, rec=rec, pa=pa,
                                          dt=kwargs.pop('dt', self.dt), **kwargs)
        else:
            summary = self.op_adj().apply(src=srca, rec=rec, pa=pa,
                                          dt=kwargs.pop('dt', self.dt), **kwargs)
        return srca, pa, summary

    def noadjoint(self, rec, srca=None, va=None, pa=None, model=None, **kwargs):
        """
        Adjoint modelling function that creates the necessary
        data objects for running an adjoint modelling operator.

        Parameters
        ----------
        rec : SparseTimeFunction or array-like
            The receiver data. Please note that
            these act as the source term in the adjoint run.
        srca : SparseTimeFunction or array-like
            The resulting data for the interpolated at the
            original source location.
        pa: TimeFunction, optional
            The computed wavefield.
        model : Model, optional
            Object containing the physical parameters.

        Returns
        -------
        Adjoint source, wavefield and performance summary.
        """
        # Create a new adjoint source and receiver symbol
        srca = srca or PointSource(name='srca', grid=self.model.grid,
                                   time_range=self.geometry.time_axis,
                                   coordinates=self.geometry.src_positions)

        if self.time_order == 1:
            va = va or VectorTimeFunction(name="va", grid=self.model.grid,
                                          time_order=self.time_order,
                                          space_order=self.space_order)
            kwargs.update({k.name: k for k in va})

        pa = pa or TimeFunction(name="pa", grid=self.model.grid,
                                time_order=self.time_order, space_order=self.space_order,
                                staggered=NODE)

        model = model or self.model
        # Pick vp and physical parameters from model unless explicitly provided
        kwargs.update(model.physical_params(**kwargs))

        # Execute operator and return wavefield and receiver data
        summary = self.op_noadj().apply(src=srca, rec=rec, pa=pa,
                                        dt=kwargs.pop('dt', self.dt), **kwargs)
        return srca, pa, summary

    def jacobian_adjoint(self, rec, p, v=None, pa=None, grad_m=None,
                         grad_tau=None, r=None, va=None,
                         model=None, checkpointing=False, **kwargs):
        """
        Gradient modelling function for computing the adjoint of the
        Linearized Born modelling function, ie. the action of the
        Jacobian adjoint on an input data.

        Parameters
        ----------
        rec : SparseTimeFunction
            Receiver data.
        p : TimeFunction
            Full wavefield `p` (created with save=True).
        pa : TimeFunction, optional
            Stores the computed wavefield.
        grad : Function, optional
            Stores the gradient field.
        r : TimeFunction, optional
            The computed attenuation memory variable.
        va : VectorTimeFunction, optional
            The computed particle velocity.
        model : Model, optional
            Object containing the physical parameters.
        vp : Function or float, optional
            The time-constant velocity.
        qp : Function, optional
            The P-wave quality factor.
        b : Function, optional
            The time-constant inverse density.

        Returns
        -------
        Gradient field and performance summary.
        """
        dt = kwargs.pop('dt', self.dt)
        # Gradient symbol
        grad_m = grad_m or Function(name='grad_m', grid=self.model.grid)
        grad_tau = grad_tau or Function(name='grad_tau', grid=self.model.grid)

        # Create the forward wavefield
        pa = pa or TimeFunction(name='pa', grid=self.model.grid,
                                time_order=self.time_order, space_order=self.space_order,
                                staggered=NODE)

        model = model or self.model
        # Pick vp and physical parameters from model unless explicitly provided
        kwargs.update(model.physical_params(**kwargs))

        if checkpointing:
            if self.time_order == 1:
                v = VectorTimeFunction(name="v", grid=self.model.grid,
                                       time_order=self.time_order,
                                       space_order=self.space_order)
                kwargs.update({k.name: k for k in v})

            # p = TimeFunction(name='p', grid=self.model.grid,
            #                  time_order=self.time_order, space_order=self.space_order,
            #                  staggered=NODE)

            # if self.kernel == 'sls':

            #     r = TimeFunction(name="r", grid=self.model.grid,
            #                      time_order=self.time_order,
            #                      space_order=self.space_order, staggered=NODE)

            #     l = [p, r] + v.values() if self.time_order == 1 else [p, r]
            #     cp = DevitoCheckpoint(l)
            #     n_checkpoints = None
            #     wrap_fw = CheckpointOperator(self.op_fwd(save=False),
            #                                  src=self.geometry.src, p=p, r=r, dt=dt,
            #                                  **kwargs)
            # else:
            #     l = [p] + v.values() if self.time_order == 1 else [p]
            #     cp = DevitoCheckpoint(l)
            #     n_checkpoints = None
            #     wrap_fw = CheckpointOperator(self.op_fwd(save=False),
            #                                  src=self.geometry.src, p=p,
            #                                  dt=dt, **kwargs)

            # if self.kernel == 'sls':

            #     ra = TimeFunction(name="ra", grid=self.model.grid,
            #                       time_order=self.time_order,
            #                       space_order=self.space_order, staggered=NODE)

            #     if self.time_order == 1:
            #         for i in {k.name: k for k in v}.keys():
            #             kwargs.pop(i)
            #         va = VectorTimeFunction(name="va", grid=self.model.grid,
            #                                 time_order=self.time_order,
            #                                 space_order=self.space_order)
            #         kwargs.update({k.name: k for k in va})
            #         kwargs['time_m'] = 0

            #     wrap_rev = CheckpointOperator(self.op_grad(save=False), p=p,
            #                                   pa=pa, r=ra,
            #                                   rec=rec, dt=dt, grad=grad, **kwargs)
            # else:
            #     l = [p] + v.values() if self.time_order == 1 else [p]
            #     cp = DevitoCheckpoint(l)
            #     n_checkpoints = None
            #     if self.kernel == 'acoustic':
            #         kwargs.pop('qp', None)

            #     if self.time_order == 1:
            #         for i in {k.name: k for k in v}.keys():
            #             kwargs.pop(i)
            #         va = VectorTimeFunction(name="va", grid=self.model.grid,
            #                                 time_order=self.time_order,
            #                                 space_order=self.space_order)
            #         kwargs.update({k.name: k for k in va})
            #         kwargs['time_m'] = 0

            #     wrap_rev = CheckpointOperator(self.op_grad(save=False), p=p, pa=pa,
            #                                   rec=rec, dt=dt, grad=grad, **kwargs)

            # # Run forward
            # wrp = Revolver(cp, wrap_fw, wrap_rev, n_checkpoints,
            #                rec.data.shape[0] - (1 if self.time_order == 1 else 2))
            # wrp.apply_forward()
            # summary = wrp.apply_reverse()
        else:
            if self.time_order == 1:
                va = va or VectorTimeFunction(name="va", grid=self.model.grid,
                                              time_order=self.time_order,
                                              space_order=self.space_order)
                kwargs.update({k.name: k for k in va})

                save_t = rec.nt
                v = v or VectorTimeFunction(name="v", grid=self.model.grid, save=save_t,
                                            time_order=self.time_order,
                                            space_order=self.space_order)
                kwargs.update({k.name: k for k in v})
                kwargs['time_m'] = 0

            if self.kernel == 'sls':
                # Memory variable:
                r = r or TimeFunction(name="r", grid=self.model.grid,
                                      time_order=self.time_order,
                                      space_order=self.space_order, staggered=NODE)

                summary = self.op_grad().apply(rec=rec, grad_m=grad_m,
                                               grad_tau=grad_tau,
                                               pa=pa, p=p, r=r, dt=dt,
                                               **kwargs)
            # elif self.kernel == 'acoustic':
            #     kwargs.pop('qp', None)
            #     summary = self.op_grad().apply(rec=rec, grad=grad, pa=pa, p=p, dt=dt,
            #                                    **kwargs)
            # else:
            #     summary = self.op_grad().apply(rec=rec, grad=grad, pa=pa, p=p, dt=dt,
            #                                    **kwargs)
        return grad_m, grad_tau, summary

    def jacobian(self, dm=None, dtau=None, drho=None, src=None, rec=None, p=None,
                 P=None, rp=None, rP=None, v=None, dv=None, model=None, **kwargs):
        """
        Linearized Born modelling function that creates the necessary
        data objects for running an adjoint modelling operator.

        Parameters
        ----------
        src : SparseTimeFunction or array_like, optional
            Time series data for the injected source term.
        rec : SparseTimeFunction or array_like, optional
            The interpolated receiver data.
        p : TimeFunction, optional
            The forward wavefield.
        P : TimeFunction, optional
            The linearized wavefield.
        rp : TimeFunction, optional
            The computed attenuation memory variable.
        rP : TimeFunction, optional
            The computed attenuation memory variable.
        v : VectorTimeFunction, optional
            The computed particle velocity.
        dv : VectorTimeFunction, optional
            The computed particle velocity.
        model : Model, optional
            Object containing the physical parameters.
        vp : Function or float, optional
            The time-constant velocity.
        qp : Function, optional
            The P-wave quality factor.
        b : Function, optional
            The time-constant inverse density.
        """
        # Source term is read-only, so re-use the default
        src = src or self.geometry.src
        # Create a new receiver object to store the result
        rec = rec or self.geometry.rec

        # Create the forward wavefields u and U if not provided
        p = p or TimeFunction(name='p', grid=self.model.grid,
                              time_order=self.time_order, space_order=self.space_order,
                              staggered=NODE)
        P = P or TimeFunction(name='P', grid=self.model.grid,
                              time_order=self.time_order, space_order=self.space_order,
                              staggered=NODE)

        if dm is None:
            dm = Function(name='dm', grid=self.model.grid, space_order=0)
        if dtau is None:
            dtau = Function(name='dtau', grid=self.model.grid, space_order=0)

        # Memory variable:
        rp = rp or TimeFunction(name='rp', grid=self.model.grid,
                                time_order=self.time_order,
                                space_order=self.space_order, staggered=NODE)
        # Memory variable:
        rP = rP or TimeFunction(name='rP', grid=self.model.grid,
                                time_order=self.time_order,
                                space_order=self.space_order, staggered=NODE)

        if self.time_order == 1:
            v = v or VectorTimeFunction(name="v", grid=self.model.grid,
                                        time_order=self.time_order,
                                        space_order=self.space_order)
            kwargs.update({k.name: k for k in v})

            dv = dv or VectorTimeFunction(name="dv", grid=self.model.grid,
                                          time_order=self.time_order,
                                          space_order=self.space_order)
            kwargs.update({k.name: k for k in dv})

        model = model or self.model
        # Pick vp and physical parameters from model unless explicitly provided
        kwargs.update(model.physical_params(**kwargs))

        # Execute operator and return wavefield and receiver data
        if self.kernel == 'sls':
            summary = self.op_born().apply(dm=dm, dtau=dtau, p=p,
                                           P=P, src=src, rec=rec, rp=rp, rP=rP,
                                           dt=kwargs.pop('dt', self.dt),
                                           **kwargs)
        elif self.kernel == 'acoustic':
            kwargs.pop('qp', None)
            summary = self.op_born().apply(dm=dm, p=p, P=P, src=src, rec=rec,
                                           dt=kwargs.pop('dt', self.dt), **kwargs)
        else:
            summary = self.op_born().apply(dm=dm, p=p, P=P, src=src, rec=rec,
                                           dt=kwargs.pop('dt', self.dt), **kwargs)

        return rec, p, P, summary
